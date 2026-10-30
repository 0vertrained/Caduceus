"""Builds the running system from settings. The one place outside caduceus.router that wires providers in."""

from __future__ import annotations

import os
import secrets
import sqlite3
from dataclasses import dataclass, field
from pathlib import Path
from zoneinfo import ZoneInfo

from caduceus.audit.log import AuditLog
from caduceus.auth import Authenticator
from caduceus.cases import CaseRepository
from caduceus.criteria.engine import Engine
from caduceus.criteria.loader import load_library, load_lock
from caduceus.deid.safe_harbor import SafeHarborDeidentifier
from caduceus.drafting import Drafter
from caduceus.fhir.store import FhirStore, HapiFhirStore, LocalBundleStore
from caduceus.payer import MockPayer
from caduceus.router import Router
from caduceus.router.providers import AnthropicMessagesProvider, OpenAICompatibleProvider
from caduceus.workflow import Workflow


def _env(name: str, default: str | None = None):
    return field(default_factory=lambda: os.environ.get(name, default))


class InsecureConfiguration(ValueError):
    """The settings would run the service in a way that is only safe on a developer's machine."""


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
    external_api_key: str | None = _env("CADUCEUS_EXTERNAL_API_KEY")
    external_model: str = _env("CADUCEUS_EXTERNAL_MODEL", "claude-sonnet-5-5")
    # demo: the public demo. Development sign-in, synthetic data seeded in memory, reset on a schedule.
    demo_reset_hours: str = _env("CADUCEUS_DEMO_RESET_HOURS", "6")
    # Shared with Netlify: when set, only requests Netlify proxied and signed (x-nf-sign) are answered.
    proxy_signing_key: str | None = _env("CADUCEUS_PROXY_SIGNING_KEY")

    def check(self) -> None:
        if self.auth_mode not in ("dev", "oidc", "demo"):
            raise InsecureConfiguration("CADUCEUS_AUTH_MODE must be dev, demo or oidc.")
        if self.proxy_signing_key is not None and len(self.proxy_signing_key.encode()) < 32:
            raise InsecureConfiguration("CADUCEUS_PROXY_SIGNING_KEY must be at least 32 bytes.")
        if self.auth_mode == "demo" and self.external_api_key:
            raise InsecureConfiguration("The public demo must not hold a model API key: anyone could spend it.")
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
    audit: AuditLog
    cases: CaseRepository
    store: FhirStore
    workflow: Workflow
    authenticator: Authenticator
    router: Router | None


def build_services(settings: Settings | None = None, conn: sqlite3.Connection | None = None) -> Services:
    s = settings or Settings()
    s.check()
    if conn is None:
        if s.db_path != ":memory:":
            Path(s.db_path).parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(s.db_path, check_same_thread=False)
    audit, cases = AuditLog(conn), CaseRepository(conn)
    store: FhirStore = HapiFhirStore(s.fhir_base_url) if s.fhir_base_url else LocalBundleStore(s.data_dir)
    policies = s.data_dir / "policies"
    library, engine = load_library(policies), Engine(load_lock(policies))

    key = s.deid_key.encode() if s.deid_key else secrets.token_bytes(32)  # dev: a fresh key per process
    deid = SafeHarborDeidentifier(key)
    in_vpc = OpenAICompatibleProvider(s.in_vpc_url, s.in_vpc_model) if s.in_vpc_url else None
    external = AnthropicMessagesProvider(s.external_api_key, s.external_model) if s.external_api_key else None
    router = Router(in_vpc, external, deid) if (in_vpc or external) else None

    workflow = Workflow(cases, audit, store, library, engine, Drafter(router, deid), MockPayer())
    auth = Authenticator(s.auth_mode, s.oidc_jwks_url, s.oidc_issuer, s.oidc_audience)
    return Services(s, ZoneInfo(s.clinic_tz), audit, cases, store, workflow, auth, router)
