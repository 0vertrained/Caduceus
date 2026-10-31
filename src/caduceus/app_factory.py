"""Builds the running system from settings. The one place outside caduceus.router that wires providers in."""

from __future__ import annotations

import json
import os
import re
import secrets
import sqlite3
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any
from urllib.parse import urlsplit
from zoneinfo import ZoneInfo

from caduceus.audit.log import AuditLog
from caduceus.auth import Authenticator
from caduceus.cases import CaseRepository, DocumentRepository, EhrRecordRepository, FaxRepository
from caduceus.criteria.engine import Engine
from caduceus.criteria.loader import active, load_library, load_lock, needs_side
from caduceus.deid.safe_harbor import SafeHarborDeidentifier
from caduceus.drafting import Drafter
from caduceus.fhir.store import FhirStore, HapiFhirStore, LocalBundleStore, WithEhrRecords, WithFiledDocuments
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
from caduceus.smart import SANDBOX_ISS, safe_url
from caduceus.storage import AuditStore, CaseStore, DocumentStore, FaxStore
from caduceus.workflow import Workflow

if TYPE_CHECKING:
    from caduceus.postgres import PostgresStore


def _env(name: str, default: str | None = None):
    return field(default_factory=lambda: os.environ.get(name, default))


class InsecureConfiguration(ValueError):
    """The settings would run the service in a way that is only safe on a developer's machine."""


DEFAULT_MODELS = {"anthropic": "claude-sonnet-5-5", "ollama": "gpt-oss:120b", "stub": "stub-1"}
FIXED_QUOTA = {"ollama"}  # providers whose keys cannot run up a bill: the only kind the public demo may hold

# OIDC sign-in. The issuer goes into the page's Content Security Policy and the client id to the browser, so both are
# held to a plain form: no spaces, quotes, semicolons or commas, nothing that could end a header value or a directive,
# and no user name or password in the address.
ISSUER_CHARACTERS = re.compile(r"[A-Za-z0-9._~:/%\[\]-]+")
CLIENT_ID = re.compile(r"[A-Za-z0-9._:-]{1,128}")
SCOPE_TOKEN = re.compile(r"[!#-\[\]-~]+")  # RFC 6749 section 3.3
LOOPBACK = {"localhost", "127.0.0.1", "::1"}


def _plain_address(address: str) -> bool:
    """https://host[:port][/path], or http:// on this machine, written with nothing that would need quoting."""
    if not ISSUER_CHARACTERS.fullmatch(address):
        return False
    try:
        url = urlsplit(address)
        url.port  # noqa: B018 - raises ValueError for a port that is not a number in range
    except ValueError:
        return False
    return bool(url.hostname) and not url.query and not url.fragment and (
        url.scheme == "https" or (url.scheme == "http" and url.hostname in LOOPBACK))


@dataclass
class Settings:
    """Read from the environment when constructed. Secure by default: OIDC sign-in unless development or demo
    sign-in is asked for by name, and with OIDC a fixed de-identification key is required."""

    data_dir: Path = field(default_factory=lambda: Path(os.environ.get("CADUCEUS_DATA_DIR", "data")))
    db_path: str = _env("CADUCEUS_DB", "var/caduceus.sqlite3")
    # postgresql://user:password@host:port/database. When set, cases and the audit log live in Postgres and db_path
    # is not used (docs/postgres.md). Left out of the settings' repr, since it can hold a password.
    database_url: str | None = field(default_factory=lambda: os.environ.get("CADUCEUS_DATABASE_URL") or None,
                                     repr=False)
    clinic_tz: str = _env("CADUCEUS_CLINIC_TZ", "America/Denver")
    auth_mode: str = _env("CADUCEUS_AUTH_MODE", "oidc")
    oidc_jwks_url: str | None = _env("CADUCEUS_OIDC_JWKS_URL")
    oidc_issuer: str | None = _env("CADUCEUS_OIDC_ISSUER")
    oidc_audience: str | None = _env("CADUCEUS_OIDC_AUDIENCE")
    # The review UI's own client at the identity provider: public (it holds no secret), Authorization Code with PKCE.
    # The browser learns it, the issuer and the scope from /api/auth-config (docs/sign-in.md).
    oidc_client_id: str | None = _env("CADUCEUS_OIDC_CLIENT_ID")
    # What the UI asks the provider for. Some providers issue a refresh token only with offline_access added; Keycloak
    # issues one without it, and with it would issue one that outlives the session.
    oidc_scope: str = _env("CADUCEUS_OIDC_SCOPE", "openid profile")
    deid_key: str | None = _env("CADUCEUS_DEID_KEY")
    fhir_base_url: str | None = _env("CADUCEUS_FHIR_BASE_URL")
    in_vpc_url: str | None = _env("CADUCEUS_IN_VPC_URL")
    in_vpc_model: str = _env("CADUCEUS_IN_VPC_MODEL", "llama3.1:8b")
    # Whether the in-VPC model also writes de-identified drafts when the external model is off or absent. "0" keeps
    # it for PHI work alone, as the demo does: a small model on a CPU would hold a request past its time limit.
    in_vpc_drafts: str = _env("CADUCEUS_IN_VPC_DRAFTS", "1")
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
    # Opening Caduceus from an EHR by SMART launch (docs/smart-launch.md). The FHIR servers it may be launched from,
    # as base URLs separated by spaces or commas: in the demo the public SMART sandbox unless set, and nowhere
    # outside it unless set. The launch is off until a redirect URI is set too: this site's /smart/callback.
    smart_iss: str | None = _env("CADUCEUS_SMART_ISS")
    smart_redirect_uris: str | None = _env("CADUCEUS_SMART_REDIRECT_URIS")
    smart_client_id: str = _env("CADUCEUS_SMART_CLIENT_ID", "caduceus-demo")

    @property
    def smart_allowed(self) -> tuple[str, ...]:
        if self.smart_iss is not None:
            return tuple(u for u in re.split(r"[\s,]+", self.smart_iss) if u)
        return SANDBOX_ISS if self.auth_mode == "demo" else ()

    @property
    def smart_redirects(self) -> tuple[str, ...]:
        return tuple(u for u in re.split(r"[\s,]+", self.smart_redirect_uris or "") if u)

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
        if self.database_url:
            if self.auth_mode == "demo":
                raise InsecureConfiguration("The public demo keeps each visitor's copy of the cases in memory, never "
                                            "in a database: unset CADUCEUS_DATABASE_URL.")
            if not self.database_url.startswith(("postgresql://", "postgres://")):
                raise InsecureConfiguration("CADUCEUS_DATABASE_URL must be a postgresql:// URL.")
        for url in self.smart_allowed + self.smart_redirects:
            if not safe_url(url) or urlsplit(url).query:
                raise InsecureConfiguration(f"{url[:80]} is not a safe SMART launch address: use https (plain http "
                                            "only to this machine), with no query, user name or fragment.")
        if not re.fullmatch(r"[A-Za-z0-9._-]{1,100}", self.smart_client_id):
            raise InsecureConfiguration("CADUCEUS_SMART_CLIENT_ID must be letters, digits, dots, dashes or "
                                        "underscores.")
        if self.auth_mode == "oidc":
            if not self.deid_key or len(self.deid_key.encode()) < 32:
                raise InsecureConfiguration("Set CADUCEUS_DEID_KEY (at least 32 bytes) outside development.")
            if self.external_api_key and not self.in_vpc_url:
                raise InsecureConfiguration("An external model is configured without an in-VPC model: "
                                            "PHI-route tasks would have nowhere to run.")
            self._check_sign_in()

    def _check_sign_in(self) -> None:
        """OIDC: what the API checks tokens against, and what the browser is told (/api/auth-config)."""
        missing = [name for name, value in (("CADUCEUS_OIDC_ISSUER", self.oidc_issuer),
                                            ("CADUCEUS_OIDC_JWKS_URL", self.oidc_jwks_url),
                                            ("CADUCEUS_OIDC_AUDIENCE", self.oidc_audience),
                                            ("CADUCEUS_OIDC_CLIENT_ID", self.oidc_client_id)) if not value]
        if missing:
            raise InsecureConfiguration(f"OIDC sign-in needs {', '.join(missing)} (docs/sign-in.md).")
        if not _plain_address(self.oidc_issuer or ""):
            raise InsecureConfiguration("CADUCEUS_OIDC_ISSUER must be the identity provider's https:// address, "
                                        "exactly as its tokens name it (http:// only on this machine: localhost or "
                                        "127.0.0.1).")
        if not CLIENT_ID.fullmatch(self.oidc_client_id or ""):
            raise InsecureConfiguration("CADUCEUS_OIDC_CLIENT_ID must be the UI's client id at the identity provider: "
                                        "up to 128 letters, digits and . _ : -, with no spaces.")
        if self.oidc_client_id == self.oidc_audience:
            raise InsecureConfiguration("CADUCEUS_OIDC_AUDIENCE must name the API, not the UI's client: a token issued "
                                        "to the UI, such as its ID token, must never pass as one for the API.")
        scopes = self.oidc_scope.split()
        if "openid" not in scopes or not all(SCOPE_TOKEN.fullmatch(s) for s in scopes):
            raise InsecureConfiguration("CADUCEUS_OIDC_SCOPE must be scopes separated by spaces, openid among them.")


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
    database: PostgresStore | None = None  # its connection pool, when the settings chose Postgres

    def close(self) -> None:
        """Give back the Postgres connections. A SQLite connection stays open for whoever holds it, as before."""
        if self.database is not None:
            self.database.close()


def build_services(settings: Settings | None = None, conn: sqlite3.Connection | None = None) -> Services:
    """`conn` is a SQLite connection to use (the demo's copies, the tests, the evals). Without one, the settings
    choose: Postgres when CADUCEUS_DATABASE_URL is set, otherwise the SQLite file at CADUCEUS_DB."""
    s = settings or Settings()
    s.check()
    database: PostgresStore | None = None
    audit: AuditStore
    cases: CaseStore
    filed: DocumentStore
    faxes: FaxStore
    launched: EhrRecordRepository | None = None  # records shared by an EHR launch; SQLite only for now
    if conn is None and s.database_url:
        from caduceus.postgres import connect  # the driver loads only when a deployment asks for Postgres
        database = connect(s.database_url)
        audit, cases, filed, faxes = database.audit, database.cases, database.documents, database.faxes
    else:
        if conn is None:
            if s.db_path != ":memory:":
                Path(s.db_path).parent.mkdir(parents=True, exist_ok=True)
            conn = sqlite3.connect(s.db_path, check_same_thread=False)
        audit, cases, filed, faxes = AuditLog(conn), CaseRepository(conn), DocumentRepository(conn), FaxRepository(conn)
        launched = EhrRecordRepository(conn)
    record: FhirStore = (HapiFhirStore(s.fhir_base_url) if s.fhir_base_url
                         else LocalBundleStore(*s.scenario_dirs()))
    store: FhirStore = WithFiledDocuments(record, filed)
    if launched is not None:
        store = WithEhrRecords(store, launched)
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
    router = Router(in_vpc, external, deid, drafts_in_vpc=s.in_vpc_drafts != "0") if (in_vpc or external) else None
    if router is not None:  # an administrator's last switch of the external route holds across restarts
        switched = [e for e in audit.events() if e.action == "admin.external_route"]
        if switched:
            router.switch_external(bool(switched[-1].detail.get("on")))

    tz = ZoneInfo(s.clinic_tz)
    workflow = Workflow(cases, audit, store, library, engine, Drafter(router, deid),
                        MockPayer(published={c.key: c.content_sha256() for c in current.values()},
                                  review_seconds=float(s.payer_review_seconds)),
                        faxes=faxes, catalog=_catalog(*s.scenario_dirs(), current=current),
                        fax_dir=s.data_dir / "faxes", tz=tz, filed=filed, launched=launched)
    auth = Authenticator(s.auth_mode, s.oidc_jwks_url, s.oidc_issuer, s.oidc_audience)
    return Services(s, tz, audit, cases, store, workflow, auth, router, conn, database)


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

    # Who orders each service on this team: the clinician a case opened from an EHR goes to for anything the record
    # does not answer.
    ordering: dict[str, dict[str, str]] = {}
    for c in sorted(cases, key=lambda c: c["id"]):
        ordering.setdefault(c["service"]["code"], c["ordering_clinician"])
    return {"services": {c["service"]["code"]: service(c) for c in cases},
            "clinicians": clinicians, "ordering": ordering,
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
