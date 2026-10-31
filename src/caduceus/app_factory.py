"""Builds the running system from settings. The one place outside caduceus.router that wires providers in."""

from __future__ import annotations

import json
import os
import secrets
import sqlite3
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from caduceus.audit.log import AuditLog
from caduceus.auth import Authenticator
from caduceus.cases import CaseRepository, DocumentRepository, FaxRepository
from caduceus.criteria.engine import Engine
from caduceus.criteria.loader import active, load_library, load_lock, needs_side
from caduceus.deid.safe_harbor import SafeHarborDeidentifier
from caduceus.drafting import Drafter
from caduceus.fhir.store import FhirStore, HapiFhirStore, LocalBundleStore, WithFiledDocuments
from caduceus.payer import MockPayer
from caduceus.router import Router
from caduceus.router.providers import (
    AnthropicMessagesProvider,
    CachingProvider,
    OllamaChatProvider,
    OpenAICompatibleProvider,
    Provider,
    StubProvider,
)
from caduceus.storage import AuditStore, CaseStore
from caduceus.workflow import Workflow


def _env(name: str, default: str | None = None):
    return field(default_factory=lambda: os.environ.get(name, default))


class InsecureConfiguration(ValueError):
    """The settings would run the service in a way that is only safe on a developer's machine."""


DEFAULT_MODELS = {"anthropic": "claude-sonnet-5-5", "ollama": "gpt-oss:120b", "stub": "stub-1"}
FIXED_QUOTA = {"ollama"}  # providers whose keys cannot run up a bill: the only kind the public demo may hold


@dataclass
class Settings:
    """Read from the environment when constructed. Secure by default: OIDC sign-in unless development or demo
    sign-in is asked for by name, and with OIDC a fixed de-identification key is required."""

    data_dir: Path = field(default_factory=lambda: Path(os.environ.get("CADUCEUS_DATA_DIR", "data")))
    db_path: str = _env("CADUCEUS_DB", "var/caduceus.sqlite3")
    clinic_tz: str = _env("CADUCEUS_CLINIC_TZ", "America/Denver")
    auth_mode: str = _env("CADUCEUS_AUTH_MODE", "oidc")
    oidc_jwks_url: str | None = _env("CADUCEUS_OIDC_JWKS_URL")
    oidc_issuer: str | None = _env("CADUCEUS_OIDC_ISSUER")
    oidc_audience: str | None = _env("CADUCEUS_OIDC_AUDIENCE")
    deid_key: str | None = _env("CADUCEUS_DEID_KEY")
    fhir_base_url: str | None = _env("CADUCEUS_FHIR_BASE_URL")
    in_vpc_url: str | None = _env("CADUCEUS_IN_VPC_URL")
    in_vpc_model: str = _env("CADUCEUS_IN_VPC_MODEL", "llama3.1:8b")
    # The external model only ever sees de-identified, stamped text (the router enforces it). anthropic is billed
    # per token; ollama (Ollama Cloud) is a subscription with fixed limits; stub is for development and tests.
    external_provider: str = _env("CADUCEUS_EXTERNAL_PROVIDER", "anthropic")
    external_base_url: str | None = _env("CADUCEUS_EXTERNAL_BASE_URL")
    external_api_key: str | None = _env("CADUCEUS_EXTERNAL_API_KEY")
    external_model: str | None = _env("CADUCEUS_EXTERNAL_MODEL")
    # Seconds to wait for the external model. The demo keeps it under Netlify's 26-second proxy limit, so a slow
    # model makes a draft fall back to the template instead of making a person wait; recording allows longer.
    external_timeout: str = _env("CADUCEUS_EXTERNAL_TIMEOUT", "60")
    # Seconds the mock payer takes to decide (half when expedited). 0 decides at once, for tests and local work.
    payer_review_seconds: str = _env("CADUCEUS_PAYER_REVIEW_SECONDS", "0")
    # Recorded external replies (scripts/record_completions.py). The demo uses data/recorded/completions.json.
    model_recordings: str | None = _env("CADUCEUS_MODEL_RECORDINGS")
    # demo: the public demo. Development sign-in, synthetic data seeded in memory, reset on a schedule.
    demo_reset_hours: str = _env("CADUCEUS_DEMO_RESET_HOURS", "6")
    # Shared with Netlify: when set, only requests Netlify proxied and signed (x-nf-sign) are answered.
    proxy_signing_key: str | None = _env("CADUCEUS_PROXY_SIGNING_KEY")
    # 1 adds the showcase cases (data/showcase, scripts/build_showcase.py) after the dataset's own: the public demo
    # turns it on. Off by default, so the tests and the eval gate see the dataset alone. Local bundles only.
    showcase: str = _env("CADUCEUS_SHOWCASE", "0")

    def scenario_dirs(self) -> list[Path]:
        """Where the scenarios and their records are: the data directory, then the showcase when it is on."""
        on = self.showcase.strip().lower() in ("1", "true", "yes")
        return [self.data_dir, *([self.data_dir / "showcase"] if on else [])]

    def check(self) -> None:
        if self.auth_mode not in ("dev", "oidc", "demo"):
            raise InsecureConfiguration("CADUCEUS_AUTH_MODE must be dev, demo or oidc.")
        if self.proxy_signing_key is not None and len(self.proxy_signing_key.encode()) < 32:
            raise InsecureConfiguration("CADUCEUS_PROXY_SIGNING_KEY must be at least 32 bytes.")
        if self.external_provider not in DEFAULT_MODELS:
            raise InsecureConfiguration("CADUCEUS_EXTERNAL_PROVIDER must be anthropic, ollama or stub.")
        if self.external_provider == "stub" and self.auth_mode != "dev":
            raise InsecureConfiguration("The stub model is for development and tests only.")
        if self.auth_mode == "demo" and self.external_api_key and self.external_provider not in FIXED_QUOTA:
            raise InsecureConfiguration("The public demo must not hold a metered model API key: anyone could spend "
                                        "it. Use a key with a fixed quota (Ollama Cloud).")
        if self.auth_mode == "oidc":
            if not self.deid_key or len(self.deid_key.encode()) < 32:
                raise InsecureConfiguration("Set CADUCEUS_DEID_KEY (at least 32 bytes) outside development.")
            if self.external_api_key and not self.in_vpc_url:
                raise InsecureConfiguration("An external model is configured without an in-VPC model: "
                                            "PHI-route tasks would have nowhere to run.")


@dataclass
class Services:
    settings: Settings
    tz: ZoneInfo
    audit: AuditStore
    cases: CaseStore
    store: FhirStore
    workflow: Workflow
    authenticator: Authenticator
    router: Router | None
    conn: sqlite3.Connection | None = None


def build_services(settings: Settings | None = None, conn: sqlite3.Connection | None = None) -> Services:
    s = settings or Settings()
    s.check()
    if conn is None:
        if s.db_path != ":memory:":
            Path(s.db_path).parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(s.db_path, check_same_thread=False)
    audit, cases, filed = AuditLog(conn), CaseRepository(conn), DocumentRepository(conn)
    record: FhirStore = (HapiFhirStore(s.fhir_base_url) if s.fhir_base_url
                         else LocalBundleStore(*s.scenario_dirs()))
    store = WithFiledDocuments(record, filed)
    policies = s.data_dir / "policies"
    lock = load_lock(policies)
    library, engine = load_library(policies), Engine(lock)
    current = active(library, lock)  # the set each service runs under now

    key = s.deid_key.encode() if s.deid_key else secrets.token_bytes(32)  # dev: a fresh key per process
    deid = SafeHarborDeidentifier(key)
    in_vpc = OpenAICompatibleProvider(s.in_vpc_url, s.in_vpc_model) if s.in_vpc_url else None
    external = _external_provider(s)
    recordings = (Path(s.model_recordings) if s.model_recordings
                  else s.data_dir / "recorded" / "completions.json" if s.auth_mode == "demo" else None)
    if external is not None or (recordings is not None and recordings.exists()):
        external = CachingProvider(external, recordings, model=s.external_model)
    router = Router(in_vpc, external, deid) if (in_vpc or external) else None

    tz = ZoneInfo(s.clinic_tz)
    workflow = Workflow(cases, audit, store, library, engine, Drafter(router, deid),
                        MockPayer(published={c.key: c.content_sha256() for c in current.values()},
                                  review_seconds=float(s.payer_review_seconds)),
                        faxes=FaxRepository(conn), catalog=_catalog(*s.scenario_dirs(), current=current),
                        fax_dir=s.data_dir / "faxes", tz=tz, filed=filed)
    auth = Authenticator(s.auth_mode, s.oidc_jwks_url, s.oidc_issuer, s.oidc_audience)
    return Services(s, tz, audit, cases, store, workflow, auth, router, conn)


# Where each ordering clinician works: the clinic a faxed referral names on its "From:" line. The fax cover sheets
# (scripts/make_faxes.py) are drawn with the same names; a test keeps the two lists alike.
CLINICS = {"pract-patel": "Mesa Verde Sleep Center", "pract-lee": "Mesa Verde Orthopedics, Rio Rancho",
           "pract-reyes": "Mesa Verde Orthopedics, Santa Fe"}


def scenarios(*dirs: Path) -> list[dict]:
    """The scenarios (cases/cases.json) of each data directory, in order."""
    return [c for d in dirs if (d / "cases" / "cases.json").exists()
            for c in json.loads((d / "cases" / "cases.json").read_text())]


def _catalog(*dirs: Path, current: dict | None = None) -> dict[str, Any]:
    """The services, payers and ordering clinicians a referral may name, from the scenario data, and the clinic each
    clinician works at."""
    cases = scenarios(*dirs)
    clinicians = {c["ordering_clinician"]["name"]: c["ordering_clinician"] for c in cases}
    # The health plan on file for each patient. The synthetic records hold no Coverage resource, so it comes from the
    # scenario data, and only where that data names one plan for the patient.
    plans: dict[str, set[tuple[str, str]]] = {}
    for c in cases:
        plans.setdefault(c["patient_id"], set()).add((c["payer"]["name"], c["payer"]["member_id_last4"]))
    def service(c: dict) -> dict:
        now = (current or {}).get(c["service"]["code"])
        return c["service"] | {"criteria_set": now.key if now else c["criteria_set"],
                               "needs_side": bool(now and needs_side(now))}

    return {"services": {c["service"]["code"]: service(c) for c in cases},
            "clinicians": clinicians,
            "clinics": {name: CLINICS[c["id"]] for name, c in clinicians.items() if c["id"] in CLINICS},
            "payers": {c["payer"]["name"] for c in cases},
            "coverage": {pid: {"payer": p, "member_id_last4": last4}
                         for pid, found in plans.items() if len(found) == 1 for p, last4 in found}}


def _external_provider(s: Settings) -> Provider | None:
    model = s.external_model or DEFAULT_MODELS[s.external_provider]
    if s.external_provider == "stub":
        return StubProvider(_stub_reply, name="external", model=model, vendor="a stub model (tests only)")
    if not s.external_api_key:
        return None
    if s.external_provider == "ollama":
        return OllamaChatProvider(s.external_api_key, model, base_url=s.external_base_url or "https://ollama.com",
                                  timeout=float(s.external_timeout))
    return AnthropicMessagesProvider(s.external_api_key, model,
                                     base_url=s.external_base_url or "https://api.anthropic.com")


def _stub_reply(system: str, user: str) -> str:
    """A fixed, predictable paragraph built from the de-identified facts, keeping their tokens."""
    lines = user.splitlines()
    if "reconsideration" in system:
        found = {k: ln.split(": ", 1)[1] for ln in lines for k in ("Payer", "Payer's reason for denial",
                 "Coverage policy sentence", "What the record shows") if ln.startswith(k + ": ")}
        reason, policy = found.get("Payer's reason for denial", ""), found.get("Coverage policy sentence", "")
        return (f"We ask {found.get('Payer', 'the payer').rstrip('.')} to reconsider. The denial states: {reason} "
                f"The policy reads: {policy} The record shows: {found.get('What the record shows', '')} "
                "We ask that the denial be reversed.")
    who = lines[0].removeprefix("Patient: ").rstrip(".") if lines else "The patient"
    what = lines[1].removeprefix("Requested service: ").rstrip(".") if len(lines) > 1 else "the requested service"
    met = [ln[2:].split(":")[0].lower() for ln in lines if ln.startswith("- ") and ": met." in ln]
    waiting = [ln[2:].split(":")[0].lower() for ln in lines if ln.startswith("- ") and "awaiting" in ln]
    text = f"{who} is referred for {what}. The record documents {', '.join(met) or 'the criteria below'}."
    if waiting:
        text += f" The ordering clinician has been asked to confirm {', '.join(waiting)}."
    return text
