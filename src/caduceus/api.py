"""The HTTP API the review UI talks to. Every response that can carry PHI is marked no-store."""

from __future__ import annotations

import os
import re
import threading
from collections import OrderedDict
from collections.abc import Callable
from contextlib import asynccontextmanager
from contextvars import ContextVar, Token
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Annotated, Literal

import httpx
import jwt
from fastapi import Depends, FastAPI, Header, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from caduceus.app_factory import Services, Settings, build_services
from caduceus.audit.log import TamperDetected
from caduceus.auth import DEV_USERS, AuthError, Principal, Role
from caduceus.cases import EHR_RECORD, Case, Fax, StaleCase, State
from caduceus.drafting import Drafter
from caduceus.fhir.store import fax_image
from caduceus.intake import FaxReadingUnavailable
from caduceus.outcomes import outcomes
from caduceus.policies import policies
from caduceus.presenter import Presenter, clock, moment
from caduceus.ratelimit import DemoLimits
from caduceus.seed import clone_demo_services, fresh_demo_services
from caduceus.smart import LaunchRefused, Limits, SmartLaunch, lookback
from caduceus.timeline import timeline
from caduceus.tools import ToolForbidden, ToolNotAllowed
from caduceus.tour import tour
from caduceus.workflow import ActionRefused

SECURITY_HEADERS = {
    "Content-Security-Policy": "default-src 'self'; img-src 'self' data: blob:; style-src 'self'; font-src 'self'; "
                               "frame-ancestors 'none'; base-uri 'none'; form-action 'self'",
    "X-Content-Type-Options": "nosniff",
    "Referrer-Policy": "no-referrer",
    "Strict-Transport-Security": "max-age=31536000; includeSubDomains",
    "Permissions-Policy": "camera=(), microphone=(), geolocation=()",
    "Cross-Origin-Opener-Policy": "same-origin",
}


def security_headers(settings: Settings) -> dict[str, str]:
    """The headers every response carries. With OIDC sign-in the page also fetches the identity provider's discovery
    document and calls its token endpoint, so connect-src adds the issuer and the addresses under it, and nothing else:
    sign-in and sign-out themselves are navigations, which the policy does not govern. The development and demo
    headers stay as they are, the same as netlify.toml's."""
    if settings.auth_mode != "oidc" or not settings.oidc_issuer:
        return SECURITY_HEADERS
    policy = f"{SECURITY_HEADERS['Content-Security-Policy']}; connect-src 'self' {settings.oidc_issuer.rstrip('/')}/"
    return SECURITY_HEADERS | {"Content-Security-Policy": policy}


DEMO_RESET_COOLDOWN = timedelta(minutes=1)


SANDBOX_ID = re.compile(r"[0-9a-f]{32}")
MAX_SANDBOXES = 60           # each is a few hundred kilobytes; the least recently used goes first
TEMPLATE_MAX_AGE = timedelta(hours=6)
DEMO_EHR_RECORDS = 3         # patients opened from an EHR that one visitor's copy holds at once
# A view of a case is recorded once a minute per person: a page that refreshes itself while a payer or the model
# works is one view, not one per refresh. Someone who comes back later is recorded again.
VIEW_AGAIN = timedelta(minutes=1)
# Neither reads any data, and every page load asks the second before anything else, so the demo's limits count
# neither, and neither starts a visitor's copy of the cases.
NOT_COUNTED = {"/api/health", "/api/auth-config"}


class TooManySandboxes(Exception):
    pass


@dataclass
class Sandbox:
    svc: Services
    presenter: Presenter
    last_reset: datetime


class Live:
    """The services the API is serving right now.

    Outside the demo there is one set. In the public demo each visitor's browser sends a random sandbox id and gets
    its own copy of the seeded cases, so one visitor's clicks never change what another sees. Copies are cloned in
    milliseconds from a seeded template that is never served, are capped in number, and start over after the reset
    period. Requests without a sandbox id share one set. A request's sandbox is chosen once, in middleware, and
    handlers read through this holder."""

    def __init__(self, svc: Services, fresh: Callable[[], Services] | None):
        self._lock, self._template_lock = threading.Lock(), threading.Lock()
        self.fresh = fresh
        self.demo = svc.settings.auth_mode == "demo"
        self.reset_every = timedelta(hours=float(svc.settings.demo_reset_hours)) if self.demo else None
        self.shared = self._wrap(svc)
        self._template: tuple[Services, datetime] | None = None
        if self.demo:  # a pristine copy, taken before anyone can change the shared set
            now = svc.workflow.now()
            self._template = (clone_demo_services(svc, now, now), now)
        self._sandboxes: OrderedDict[str, Sandbox] = OrderedDict()
        self._current: ContextVar[Sandbox | None] = ContextVar("sandbox", default=None)

    @staticmethod
    def _wrap(svc: Services) -> Sandbox:
        return Sandbox(svc, Presenter(svc.store, svc.workflow.library, svc.tz), svc.workflow.now())

    @property
    def current(self) -> Sandbox:
        return self._current.get() or self.shared

    @property
    def svc(self) -> Services:
        return self.current.svc

    @property
    def presenter(self) -> Presenter:
        return self.current.presenter

    @property
    def last_reset(self) -> datetime:
        return self.current.last_reset

    def enter(self, sandbox_id: str | None, may_create: Callable[[], bool]) -> Token:
        sandbox = self._sandbox(sandbox_id, may_create) if self.demo and sandbox_id else self.shared
        return self._current.set(sandbox)

    def leave(self, token: Token) -> None:
        self._current.reset(token)

    def all(self) -> list[Sandbox]:
        with self._lock:
            return [self.shared, *self._sandboxes.values()]

    def reset(self) -> None:
        """Start the current visitor's queue over (or the shared one)."""
        if self.fresh is None:
            raise RuntimeError("only the demo can be reset")
        current = self.current
        fresh = self._wrap(self._clone(current.svc.workflow.now()))
        with self._lock:
            sid = next((k for k, v in self._sandboxes.items() if v is current), None)
            if sid is None:
                self.shared = fresh
            else:
                self._sandboxes[sid] = fresh
        self._current.set(fresh)

    def expire(self) -> None:
        """Drop visitors' copies older than the reset period, and start the shared set over when it is."""
        now = self.shared.svc.workflow.now()
        with self._lock:
            for sid in [k for k, v in self._sandboxes.items() if now - v.last_reset >= self.reset_every]:
                del self._sandboxes[sid]
            stale = now - self.shared.last_reset >= self.reset_every  # type: ignore[operator]
        if stale:
            fresh = self._wrap(self._clone(now))
            with self._lock:
                self.shared = fresh

    def _sandbox(self, sandbox_id: str, may_create: Callable[[], bool]) -> Sandbox:
        now = self.shared.svc.workflow.now()
        with self._lock:
            sandbox = self._sandboxes.get(sandbox_id)
            if sandbox is not None and now - sandbox.last_reset < self.reset_every:  # type: ignore[operator]
                self._sandboxes.move_to_end(sandbox_id)
                return sandbox
        if not may_create():
            raise TooManySandboxes
        sandbox = self._wrap(self._clone(now))
        with self._lock:
            sandbox = self._sandboxes.setdefault(sandbox_id, sandbox)  # two first requests at once share one copy
            self._sandboxes.move_to_end(sandbox_id)
            while len(self._sandboxes) > MAX_SANDBOXES:
                self._sandboxes.popitem(last=False)
        return sandbox

    def _clone(self, now: datetime) -> Services:
        with self._template_lock:
            template, seeded_at = self._template  # type: ignore[misc]
            if self.fresh is not None and now - seeded_at > TEMPLATE_MAX_AGE:
                template, seeded_at = self.fresh(), now
                self._template = (template, seeded_at)
            return clone_demo_services(template, now, seeded_at)


def visitor_of(request: Request) -> str:
    """Behind Netlify the visitor's address is in the forwarded headers, not the connection."""
    return (request.headers.get("x-nf-client-connection-ip")
            or request.headers.get("x-forwarded-for", "").split(",")[0].strip()
            or (request.client.host if request.client else "unknown"))


# Free text is bounded here so a request cannot park megabytes in a case; the workflow gives the friendly limits.
NO_SUMMARY = {"text": "", "route": None, "model": None}  # after signing, the summary is not shown again


class SwitchBody(BaseModel):
    on: bool


class ReturnBody(BaseModel):
    reason: str = Field(max_length=2_000)


class AttestBody(BaseModel):
    answers: dict[str, str]


class DraftBody(BaseModel):
    text: str = Field(max_length=20_000)


class NoteBody(BaseModel):
    text: str = Field(max_length=20_000)


class AppealBody(BaseModel):
    text: str = Field(max_length=20_000)


class UrgencyBody(BaseModel):
    expedited: bool
    reason: str = Field(default="", max_length=500)


class ConfirmFaxBody(BaseModel):
    values: dict[str, Annotated[str, Field(max_length=300)]] = Field(max_length=20)
    # The fields sent to a person that they ticked as matching the fax. The server refuses the case until each one
    # is ticked or corrected.
    checked: list[Annotated[str, Field(max_length=40)]] = Field(default_factory=list, max_length=20)


class AskClinicBody(BaseModel):
    missing: list[Annotated[str, Field(max_length=40)]] = Field(default_factory=list, max_length=10)
    resend: bool = False
    referral: str | None = Field(default=None, max_length=9)  # the number as the person has it, to name in the request


class MatchBody(BaseModel):
    """A patient lookup. The name travels in the body, never the URL, so it stays out of access logs."""
    name: str = Field(max_length=120)
    birth_date: str = Field(max_length=10)
    ref: str | None = Field(default=None, max_length=32)


class LaunchBody(BaseModel):
    """What the EHR put on Caduceus's launch address, sent on in the body so it stays out of access logs."""
    iss: str = Field(min_length=1, max_length=512)
    launch: str = Field(min_length=1, max_length=2048)
    redirect_uri: str = Field(min_length=1, max_length=512)


class CallbackBody(BaseModel):
    code: str = Field(min_length=1, max_length=2048)
    state: str = Field(min_length=1, max_length=64)


class EhrCaseBody(BaseModel):
    service: str = Field(max_length=10)
    side: Literal["left", "right"] | None = None
    payer: str = Field(max_length=100)


def create_app(services: Services | None = None, dispatch_interval: float | None = 5.0,
               fresh_demo: Callable[[], Services] | None = None, ehr_transport: httpx.BaseTransport | None = None,
               ehr_limits: Limits | None = None) -> FastAPI:
    """dispatch_interval=None (tests) starts no background threads: no outbox dispatcher, no demo resets.
    ehr_transport stands in for the network to the EHR (tests pass httpx.MockTransport)."""
    built: Services | None = None  # what this app builds, it closes when it stops; services handed in stay open
    if services is None:
        settings = Settings()
        services = built = fresh_demo_services(settings) if settings.auth_mode == "demo" else build_services(settings)
    if fresh_demo is None and services.settings.auth_mode == "demo":
        demo_settings = services.settings
        fresh_demo = lambda: fresh_demo_services(demo_settings)  # noqa: E731
    live = Live(services, fresh_demo)
    stop = threading.Event()

    def safe_dispatch(svc: Services | None = None) -> None:
        """Reading a case must never fail because sending another one did; failures are audited instead."""
        svc = svc or live.svc
        try:
            svc.workflow.dispatch_due()
        except Exception as e:  # noqa: BLE001
            try:
                svc.audit.append("caduceus-agent", "agent_runtime", "dispatch.error", None,
                                 {"error": type(e).__name__})
            except Exception:  # noqa: BLE001, S110 - the audit store itself is unavailable; nothing safer to do
                pass

    def dispatcher() -> None:
        while not stop.wait(dispatch_interval or 5.0):
            for sandbox in live.all():
                safe_dispatch(sandbox.svc)

    def demo_resetter() -> None:
        while not stop.wait(300):
            live.expire()

    @asynccontextmanager
    async def lifespan(_: FastAPI):
        if dispatch_interval:
            threading.Thread(target=dispatcher, name="outbox", daemon=True).start()
            if live.demo:
                threading.Thread(target=demo_resetter, name="demo-reset", daemon=True).start()
        yield
        stop.set()
        if built is not None:
            built.close()  # hands the Postgres connections back; nothing to do on SQLite

    app = FastAPI(title="Caduceus", version="0.1.0", lifespan=lifespan, docs_url=None, redoc_url=None)
    app.state.live = live

    signing_key = live.svc.settings.proxy_signing_key
    app.state.demo_limits = DemoLimits() if live.demo else None
    settings = live.svc.settings
    smart = SmartLaunch(settings.smart_allowed, settings.smart_redirects, settings.smart_client_id,
                        transport=ehr_transport, limits=ehr_limits)
    app.state.smart = smart
    app.state.view_again = VIEW_AGAIN
    windows = lookback(live.svc.workflow.in_use())  # how far back the record is read: the criteria's own windows
    sent_headers = security_headers(live.svc.settings)

    @app.middleware("http")
    async def choose_sandbox(request: Request, call_next):
        """Registered first, so it runs last: only signed requests within the limits reach a visitor's copy."""
        sandbox_id = request.headers.get("x-caduceus-sandbox")
        if not live.demo or not request.url.path.startswith("/api/") or request.url.path in NOT_COUNTED:
            sandbox_id = None
        if sandbox_id is not None and not SANDBOX_ID.fullmatch(sandbox_id):
            return JSONResponse({"message": "That demo session id is not valid. Reload the page."}, status_code=400)
        limits = request.app.state.demo_limits
        try:
            token = live.enter(sandbox_id, lambda: limits is None or limits.new_sandbox(visitor_of(request)) == 0)
        except TooManySandboxes:
            return JSONResponse({"message": "Too many new demo sessions from your address. Wait a few minutes "
                                            "and try again."}, status_code=429, headers={"Retry-After": "300"})
        try:
            return await call_next(request)
        finally:
            live.leave(token)

    @app.middleware("http")
    async def limit_demo_requests(request: Request, call_next):
        """Runs after the signature check: only requests that came through the site count."""
        limits = request.app.state.demo_limits
        if limits is not None and request.url.path not in NOT_COUNTED:
            visitor = visitor_of(request)
            wait = limits.check(visitor, write=request.method in ("POST", "PUT", "PATCH", "DELETE"))
            if wait:
                return JSONResponse({"message": "Too many requests from your address. Wait a few minutes and "
                                                "try again."}, status_code=429, headers={"Retry-After": str(wait)})
        return await call_next(request)

    @app.middleware("http")
    async def require_proxy_signature(request: Request, call_next):
        """Behind Netlify, answer only what Netlify proxied: it signs each request with the shared key (HS256 JWS
        in x-nf-sign). Registered before the headers middleware, so refusals still get the security headers."""
        if signing_key and request.url.path != "/api/health":
            try:
                jwt.decode(request.headers.get("x-nf-sign", ""), signing_key, algorithms=["HS256"],
                           issuer="netlify", leeway=30, options={"require": ["exp", "iss"]})
            except jwt.PyJWTError:
                return JSONResponse({"message": "This API answers only through the Caduceus demo site."},
                                    status_code=403)
        return await call_next(request)

    @app.middleware("http")
    async def headers(request: Request, call_next):
        response = await call_next(request)
        for k, v in sent_headers.items():
            response.headers.setdefault(k, v)
        if request.url.path.startswith("/api/"):
            response.headers["Cache-Control"] = "no-store"
        return response

    @app.exception_handler(ActionRefused)
    async def refused(_: Request, e: ActionRefused):
        return JSONResponse({"message": str(e)} | e.detail, status_code=409)

    @app.exception_handler(StaleCase)
    async def stale(_: Request, e: StaleCase):
        return JSONResponse({"message": "Someone else changed this case. It has been reloaded."}, status_code=409)

    @app.exception_handler(ToolForbidden)
    @app.exception_handler(ToolNotAllowed)
    async def forbidden(_: Request, e: Exception):
        return JSONResponse({"message": "That action is not allowed here."}, status_code=403)

    def principal(x_caduceus_user: str | None = Header(default=None),
                  authorization: str | None = Header(default=None)) -> Principal:
        try:
            return live.svc.authenticator.authenticate(x_caduceus_user, authorization)
        except AuthError as e:
            raise HTTPException(401, "Sign in to continue.") from e

    def role(*allowed: Role):
        def check(who: Principal = Depends(principal)) -> Principal:
            if who.role not in allowed:
                raise HTTPException(403, "Your role cannot see this.")
            return who
        return check

    def load(case_id: str) -> Case:
        case = live.svc.cases.get(case_id)
        if case is None:
            raise HTTPException(404, "There is no case with that number.")
        return case

    def now() -> datetime:
        return live.svc.workflow.now()

    def fax_of(case_id: str) -> Fax | None:
        """The fax a case was created from, if it came by fax."""
        faxes = live.svc.workflow.faxes
        return next((f for f in faxes.all() if f.case_id == case_id), None) if faxes else None

    def viewed(who: Principal, action: str, case_id: str) -> None:
        """Record that a person opened a case, unless they did within the last minute (VIEW_AGAIN)."""
        last = next((e for e in reversed(live.svc.audit.events(case_id)) if e.actor == who.id and e.action == action),
                    None)
        if last is None or datetime.now(UTC) - datetime.fromisoformat(last.ts) >= app.state.view_again:
            live.svc.audit.append(who.id, who.role, action, case_id)

    def view(case: Case, who: Principal) -> dict:
        viewed(who, "case.viewed", case.id)
        out = live.presenter.case(load(case.id), now(), who.id, read_only=who.role != Role.PA_SPECIALIST)
        fax = fax_of(case.id)
        out["came_by_fax"] = fax and live.presenter.came_by_fax(
            fax, live.svc.audit.events(fax.id), live.svc.workflow.intake_form(fax)["clinic"], now())
        out["came_from_ehr"] = live.presenter.came_from_ehr(
            case, live.svc.audit.events(case.id), ehr_record_of(case), now()) \
            if EHR_RECORD.fullmatch(case.patient_id) else None
        return out

    def ehr_record_of(case: Case) -> dict | None:
        launched = live.svc.workflow.launched
        return launched.get(case.patient_id) if launched else None

    @app.get("/api/health")
    def health() -> dict:
        return {"ok": True}

    @app.get("/api/auth-config")
    def auth_config() -> dict:
        """How the browser signs in, for anyone to read: the mode, and with OIDC what the UI's public client needs to
        start Authorization Code with PKCE. Nothing secret is here, and nothing secret is needed: the UI holds none."""
        s = live.svc.settings
        if s.auth_mode != "oidc":
            return {"mode": s.auth_mode}
        return {"mode": "oidc", "issuer": s.oidc_issuer, "client_id": s.oidc_client_id, "scope": s.oidc_scope}

    @app.get("/api/demo")
    def demo_info() -> dict:
        """Tells the UI whether to show the demo banner. Says nothing in a real deployment."""
        if not live.demo:
            return {"demo": False}
        return {"demo": True, "reset_every_hours": live.reset_every.total_seconds() / 3600,  # type: ignore[union-attr]
                "last_reset_at": live.last_reset.isoformat(),
                "next_reset_at": (live.last_reset + live.reset_every).isoformat(),  # type: ignore[operator]
                "tour": tour(live.svc, live.presenter)}

    @app.post("/api/demo/reset")
    def demo_reset(who: Principal = Depends(principal)) -> dict:
        if not live.demo:
            raise HTTPException(404, "Not found.")
        if now() - live.last_reset < DEMO_RESET_COOLDOWN:
            raise HTTPException(429, "The demo was reset less than a minute ago. Try again shortly.")
        live.reset()
        return demo_info()

    @app.get("/api/audit/verify")
    def verify_audit(who: Principal = Depends(role(Role.AUDITOR, Role.ADMIN))) -> dict:
        try:
            return {"intact": True, "events": live.svc.audit.verify()}
        except TamperDetected as e:
            return {"intact": False, "message": str(e)}

    @app.get("/api/me")
    def me(who: Principal = Depends(principal)) -> dict:
        return {"id": who.id, "name": who.name, "role": who.role.value}

    @app.get("/api/queue")
    def queue(who: Principal = Depends(role(Role.PA_SPECIALIST, Role.ADMIN, Role.AUDITOR))) -> dict:
        safe_dispatch()
        svc = live.svc
        local_today = now().astimezone(svc.tz).date()
        approved_today = sum(1 for c in svc.cases.all() if c.submission and c.submission.get("approved_by_id") == who.id
                             and datetime.fromisoformat(c.submission["approved_at"]).astimezone(svc.tz).date()
                             == local_today)
        return live.presenter.queue(svc.cases.all(), now(), approved_today)

    @app.get("/api/cases/{case_id}")
    def get_case(case_id: str, who: Principal = Depends(role(Role.PA_SPECIALIST, Role.ADMIN, Role.AUDITOR))):
        safe_dispatch()
        return view(load(case_id), who)

    @app.get("/api/cases/{case_id}/pages/{doc_id}")
    def page(case_id: str, doc_id: str, who: Principal = Depends(role(Role.PA_SPECIALIST, Role.ADMIN, Role.AUDITOR))):
        try:
            out = live.presenter.page(doc_id, load(case_id))
        except KeyError as e:
            raise HTTPException(404, "That page is not part of this case.") from e
        live.svc.audit.append(who.id, who.role, "page.viewed", case_id, {"doc": doc_id})
        return out

    @app.get("/api/cases/{case_id}/pages/{doc_id}/image")
    def page_image(case_id: str, doc_id: str,
                   who: Principal = Depends(role(Role.PA_SPECIALIST, Role.ADMIN, Role.AUDITOR))):
        """The image of a faxed page in the case's packet (the cover sheet), from the intake folder only."""
        case = load(case_id)
        doc = live.svc.store.document(doc_id) if doc_id in case.packet else None
        file = fax_image(doc) if doc else None
        if not file or not re.fullmatch(r"FAX-\d{4}\.png", file) or live.svc.workflow.fax_dir is None:
            raise HTTPException(404, "That page has no image.")
        live.svc.audit.append(who.id, who.role, "page.viewed", case_id, {"doc": doc_id, "image": True})
        return FileResponse(live.svc.workflow.fax_dir / file, media_type="image/png")

    @app.post("/api/cases/{case_id}/request-attestation")
    def request_attestation(case_id: str, who: Principal = Depends(principal)):
        return view(live.svc.workflow.request_attestation(who, load(case_id).id), who)

    @app.post("/api/cases/{case_id}/approve")
    def approve(case_id: str, who: Principal = Depends(principal)):
        return view(live.svc.workflow.approve(who, load(case_id).id), who)

    @app.post("/api/cases/{case_id}/urgency")
    def urgency(case_id: str, body: UrgencyBody, who: Principal = Depends(principal)):
        """Ask the payer for an expedited decision (72 hours under the CMS rule), or go back to standard."""
        return view(live.svc.workflow.set_urgency(who, load(case_id).id, body.expedited, body.reason), who)

    @app.post("/api/cases/{case_id}/request-note")
    def request_note(case_id: str, who: Principal = Depends(principal)):
        return view(live.svc.workflow.request_note(who, load(case_id).id), who)

    @app.post("/api/cases/{case_id}/send-note")
    def send_note(case_id: str, who: Principal = Depends(principal)):
        return view(live.svc.workflow.approve_reply(who, load(case_id).id), who)

    @app.post("/api/cases/{case_id}/send-appeal")
    def send_appeal(case_id: str, who: Principal = Depends(principal)):
        return view(live.svc.workflow.approve_appeal(who, load(case_id).id), who)

    @app.put("/api/cases/{case_id}/appeal")
    def edit_appeal(case_id: str, body: AppealBody, who: Principal = Depends(principal)):
        return view(live.svc.workflow.edit_appeal(who, load(case_id).id, body.text), who)

    @app.post("/api/cases/{case_id}/return")
    def return_case(case_id: str, body: ReturnBody, who: Principal = Depends(principal)):
        return view(live.svc.workflow.return_for_correction(who, load(case_id).id, body.reason), who)

    @app.put("/api/cases/{case_id}/draft")
    def edit_draft(case_id: str, body: DraftBody, who: Principal = Depends(principal)):
        return view(live.svc.workflow.edit_draft(who, load(case_id).id, body.text), who)

    @app.post("/api/cases/{case_id}/remind")
    def remind(case_id: str, who: Principal = Depends(role(Role.PA_SPECIALIST))):
        return view(live.svc.workflow.remind(who, load(case_id).id), who)

    @app.post("/api/cases/{case_id}/resubmitted")
    def resubmitted(case_id: str, who: Principal = Depends(role(Role.PA_SPECIALIST))):
        return view(live.svc.workflow.resubmit(who, load(case_id).id), who)

    @app.post("/api/cases/{case_id}/recall")
    def recall(case_id: str, who: Principal = Depends(role(Role.PA_SPECIALIST))):
        return view(live.svc.workflow.recall(who, load(case_id).id), who)

    @app.post("/api/cases/{case_id}/undo")
    def undo(case_id: str, who: Principal = Depends(principal)):
        case = live.svc.workflow.undo(who, load(case_id).id)
        if who.role == Role.CLINICIAN:
            return live.presenter.attestation(case, summary_of(case), now())
        return view(case, who)

    @app.get("/api/cases/{case_id}/history")
    def history(case_id: str, who: Principal = Depends(role(Role.PA_SPECIALIST, Role.ADMIN, Role.AUDITOR))):
        load(case_id)
        return [{"seq": e.seq, "at": e.ts, "actor": e.actor, "role": e.role, "action": e.action, "detail": e.detail}
                for e in live.svc.audit.events(case_id)]

    @app.get("/api/cases/{case_id}/timeline")
    def case_timeline(case_id: str, who: Principal = Depends(role(Role.PA_SPECIALIST, Role.ADMIN, Role.AUDITOR))):
        case = load(case_id)
        events = live.svc.audit.events(case_id)
        fax = fax_of(case_id)
        if fax:  # a case that came by fax begins with the fax: received, read, checked, perhaps sent back first
            events = sorted(live.svc.audit.events(fax.id) + events, key=lambda e: e.seq)
        if EHR_RECORD.fullmatch(case.patient_id):  # one opened from the EHR begins with the EHR sharing the record
            events = sorted(live.svc.audit.events(case.patient_id) + events, key=lambda e: e.seq)
        return {"events": len(events), "can_verify": who.role in (Role.AUDITOR, Role.ADMIN),
                "entries": timeline(events, case.ordering_clinician["name"], live.svc.tz, now())}

    def fax_view(fax: Fax, detail: bool = False) -> dict:
        fields = (fax.reading or {}).get("fields", [])
        by_key = {f["key"]: f for f in fields}
        checking = live.svc.workflow.intake_form(fax) if fax.reading else None
        out = {"id": fax.id, "status": fax.status, "received": moment(fax.received_at, now(), live.svc.tz),
               "pages": len(fax.pages) + 1, "referral": by_key.get("referral", {}).get("value"),
               "patient": by_key.get("patient_name", {}).get("value"),
               "to_check": sum(1 for f in checking["fields"] if f["problem"]) if checking else 0,
               "clinic": checking and checking["clinic"], "case_id": fax.case_id, "request": None}
        if fax.request:
            labels = {f["key"]: f["label"] for f in checking["fields"]} if checking else {}
            r = fax.request
            out["request"] = {"kind": r["kind"], "missing": [labels.get(k, k) for k in r["missing"]], "to": r["to"],
                              "text": r["text"], "asked_by": r["asked_by"],
                              "asked": moment(r["asked_at"], now(), live.svc.tz),
                              "answered": r.get("answered_at") and moment(r["answered_at"], now(), live.svc.tz)}
        if detail:
            out |= {"fields": fields, "size": (fax.reading or {}).get("size"),
                    "engine": (fax.reading or {}).get("engine"), "image": f"/api/intake/{fax.id}/image"}
            if checking:
                out |= {"form": checking["fields"], "sender": checking["sender"], "choices": checking["choices"]}
        return out

    def read_new_faxes() -> bool:
        try:
            live.svc.workflow.read_faxes()
            return True
        except FaxReadingUnavailable:
            return False

    def load_fax(fax_id: str) -> Fax:
        fax = live.svc.workflow.faxes.get(fax_id) if live.svc.workflow.faxes else None
        if fax is None:
            raise HTTPException(404, "There is no fax with that number.")
        return fax

    @app.get("/api/intake")
    def intake(who: Principal = Depends(role(Role.PA_SPECIALIST, Role.ADMIN, Role.AUDITOR))):
        available = read_new_faxes()
        faxes = live.svc.workflow.faxes.all() if live.svc.workflow.faxes else []
        return {"available": available, "faxes": [fax_view(f) for f in faxes]}

    @app.get("/api/intake/{fax_id}")
    def intake_item(fax_id: str, who: Principal = Depends(role(Role.PA_SPECIALIST, Role.ADMIN, Role.AUDITOR))):
        read_new_faxes()
        fax = load_fax(fax_id)
        live.svc.audit.append(who.id, who.role, "fax.viewed", fax.id)
        return fax_view(fax, detail=True)

    @app.get("/api/intake/{fax_id}/image")
    def intake_image(fax_id: str, who: Principal = Depends(role(Role.PA_SPECIALIST, Role.ADMIN, Role.AUDITOR))):
        fax = load_fax(fax_id)
        return FileResponse(live.svc.workflow.fax_dir / fax.file, media_type="image/png")  # type: ignore[operator]

    @app.post("/api/intake/{fax_id}/match")
    def match_patient(fax_id: str, body: MatchBody, who: Principal = Depends(role(Role.PA_SPECIALIST))):
        load_fax(fax_id)
        return live.svc.workflow.match_patient(who, fax_id, body.name, body.birth_date, body.ref)

    @app.post("/api/intake/{fax_id}/ask-clinic")
    def ask_clinic(fax_id: str, body: AskClinicBody, who: Principal = Depends(role(Role.PA_SPECIALIST))):
        load_fax(fax_id)
        return fax_view(live.svc.workflow.ask_clinic(who, fax_id, body.missing, body.resend, body.referral),
                        detail=True)

    @app.post("/api/intake/{fax_id}/clinic-sent")
    def clinic_sent(fax_id: str, who: Principal = Depends(role(Role.PA_SPECIALIST))):
        load_fax(fax_id)
        return fax_view(live.svc.workflow.clinic_sent(who, fax_id), detail=True)

    @app.post("/api/intake/{fax_id}/confirm")
    def confirm_fax(fax_id: str, body: ConfirmFaxBody, who: Principal = Depends(principal)):
        load_fax(fax_id)
        case = live.svc.workflow.confirm_fax(who, fax_id, body.values, body.checked)
        return {"case_id": case.id}

    @app.get("/api/outcomes")
    def queue_outcomes(who: Principal = Depends(role(Role.PA_SPECIALIST, Role.ADMIN, Role.AUDITOR))):
        safe_dispatch()
        svc = live.svc
        sections = outcomes(svc.cases.all(), svc.audit.events(), svc.workflow.faxes.all() if svc.workflow.faxes else [])
        return {"demo": live.demo, "sections": [
            {"title": s["title"], "rows": [{"label": a, "value": b, "note": c} for a, b, c in s["rows"]]}
            for s in sections]}

    @app.get("/api/policies")
    def coverage_policies(who: Principal = Depends(role(Role.PA_SPECIALIST, Role.ADMIN, Role.AUDITOR))) -> dict:
        wf = live.svc.workflow
        return {"services": policies(wf.library, wf.engine.approved)}

    @app.get("/api/admin")
    def administration(who: Principal = Depends(role(Role.ADMIN))) -> dict:
        """How this deployment is set up, for its administrator: where patient information can go, what holds the
        cases and records, how people sign in, and whether the audit log and the policies are intact."""
        svc, s = live.svc, live.svc.settings
        events = svc.audit.events()
        try:
            intact, count = True, svc.audit.verify()
        except TamperDetected:
            intact, count = False, len(events)
        routes = svc.router.describe() if svc.router else {"in_vpc": None, "external": None, "external_on": False,
                                                            "drafts_in_vpc": False}
        return {
            "routes": routes,
            "database": ("Postgres" if svc.database is not None else
                         "SQLite in memory, a copy for each visitor" if live.demo else "SQLite"),
            "records": "A HAPI FHIR server" if s.fhir_base_url else "FHIR bundles on disk",
            "sign_in": {"oidc": "Your identity provider (OpenID Connect)",
                        "demo": "Demo sign-in: anyone can choose a person",
                        "dev": "Development sign-in"}[s.auth_mode],
            "payer": {"review_seconds": float(s.payer_review_seconds)},
            "people": [] if s.auth_mode == "oidc" else
            [{"id": p.id, "name": p.name, "role": p.role.value} for p in DEV_USERS.values()],
            "audit": {"intact": intact, "events": count,
                      "last": moment(events[-1].ts, now(), svc.tz) if events else None},
            "policies": [{"title": p["running"]["title"], "version": p["running"]["version"],
                          "intact": p["running"]["intact"]}
                         for p in policies(svc.workflow.library, svc.workflow.engine.approved)],
        }

    @app.post("/api/admin/external-route")
    def switch_external_route(body: SwitchBody, who: Principal = Depends(role(Role.ADMIN))) -> dict:
        """Stop, or restart, every call to the external model. Kept in the audit log, which the next start reads."""
        svc = live.svc
        if svc.router is None or not svc.router.external_enabled:
            raise HTTPException(409, "No external model is configured, so there is nothing to switch.")
        svc.router.switch_external(body.on)
        svc.audit.append(who.id, who.role, "admin.external_route", None, {"on": body.on})
        return administration(who)

    # Opening from an EHR (SMART launch, docs/smart-launch.md) --------------------------------------------------

    def ehr_step(request: Request) -> str | None:
        """Checks before a step of a launch talks to the EHR: the launch is set up here, the request names the
        visitor's own copy of the demo (a shared record never lands in the shared set), and the launch limits."""
        # Not set up, or nowhere to keep what a launch reads (the Postgres store keeps no EHR records yet): refused
        # before anything is fetched, rather than after reading a whole record only to drop it.
        if not smart.enabled or live.svc.workflow.launched is None:
            raise HTTPException(404, "Opening Caduceus from an EHR is not set up here.")
        sandbox = request.headers.get("x-caduceus-sandbox") if live.demo else None
        if live.demo and not sandbox:
            raise HTTPException(400, "Open this page from the Caduceus demo site.")
        limits = request.app.state.demo_limits
        wait = limits.ehr_launch(visitor_of(request)) if limits else 0
        if wait:
            raise HTTPException(429, "Too many launches from an EHR from your address. Wait a few minutes and try "
                                     "again.", headers={"Retry-After": str(wait)})
        return sandbox

    def launch_refused(who: Principal, e: LaunchRefused) -> JSONResponse:
        live.svc.audit.append(who.id, who.role, "ehr.launch_refused", None, e.audit)
        return JSONResponse({"message": e.message}, status_code=e.status)

    def load_record(key: str) -> dict:
        launched = live.svc.workflow.launched
        data = launched.get(key) if launched else None
        if data is None:
            raise HTTPException(404, "That record from the EHR is not here: it goes when the demo starts over. Open "
                                     "Caduceus from the EHR again.")
        return data

    @app.get("/api/smart")
    def smart_info() -> dict:
        """Whether Caduceus can be opened from an EHR here, and the client id a launcher should hold it to."""
        return {"enabled": smart.enabled, "client_id": smart.client_id if smart.enabled else None}

    @app.post("/api/smart/launch")
    def smart_launch(body: LaunchBody, request: Request, who: Principal = Depends(role(Role.PA_SPECIALIST))):
        """The EHR opened Caduceus: check where from, discover its endpoints, and say where the browser goes next."""
        sandbox = ehr_step(request)
        try:
            pending, url = smart.begin(body.iss, body.launch, body.redirect_uri, sandbox, who.id, now())
        except LaunchRefused as e:
            return launch_refused(who, e)
        live.svc.audit.append(who.id, who.role, "ehr.launch_started", None,
                              {"iss": pending.server.iss, "scope": pending.scope})
        return {"authorize_url": url}

    @app.post("/api/smart/callback")
    def smart_callback(body: CallbackBody, request: Request, who: Principal = Depends(role(Role.PA_SPECIALIST))):
        """The EHR sent the browser back: exchange the code here, read the record, file it in this copy."""
        sandbox = ehr_step(request)
        try:
            read = smart.finish(body.code, body.state, sandbox, who.id, now(), now().astimezone(live.svc.tz).date(),
                                windows)
        except LaunchRefused as e:
            return launch_refused(who, e)
        try:
            key = live.svc.workflow.file_ehr_record(who, read, keep=DEMO_EHR_RECORDS if live.demo else None)
        except (LaunchRefused, ActionRefused) as e:
            # Read, then let go: the audit log says what was read and why it was not filed, never anything from it.
            why = e.audit if isinstance(e, LaunchRefused) else {"reason": "not_filed"}
            live.svc.audit.append(who.id, who.role, "ehr.launch_refused", None,
                                  why | {"iss": read.iss, "discarded": read.counts})
            if isinstance(e, ActionRefused):
                raise
            return JSONResponse({"message": e.message}, status_code=e.status)
        live.presenter.forget(key)
        return {"record": key}

    @app.get("/api/smart/records/{key}")
    def ehr_record(key: str, who: Principal = Depends(role(Role.PA_SPECIALIST, Role.ADMIN, Role.AUDITOR))):
        data = load_record(key)
        live.svc.audit.append(who.id, who.role, "ehr.record_viewed", key)
        cases = live.svc.workflow.patient_cases(key)  # this patient's, from this read or an earlier one
        out = live.presenter.ehr_record(data, live.svc.workflow.ehr_preview(key), cases, now())
        return out | {"payers": sorted(live.svc.workflow.catalog["payers"]),
                      "read_only": who.role != Role.PA_SPECIALIST}

    @app.post("/api/smart/records/{key}/case")
    def ehr_case(key: str, body: EhrCaseBody, who: Principal = Depends(role(Role.PA_SPECIALIST))):
        load_record(key)
        return {"case_id": live.svc.workflow.open_from_ehr(who, key, body.service, body.side, body.payer).id}

    @app.get("/api/attestations")
    def my_attestations(who: Principal = Depends(role(Role.CLINICIAN))):
        mine = [c for c in live.svc.cases.all() if c.state in (State.AWAITING_ATTESTATION, State.AWAITING_NOTE)
                and c.ordering_clinician["id"] == who.clinician_id]
        out = []
        for c in sorted(mine, key=lambda c: c.due_at):  # most urgent first: the request due soonest
            row, sent = live.presenter.row(c, now()), c.reminders_since_asked()
            out.append({"case_id": c.id, "patient": row["patient"], "service": c.service["short"],
                        "kind": "note" if c.state == State.AWAITING_NOTE else "attestation", "due": row["due"],
                        "reminders": {"count": len(sent), "by": sent[-1]["by"],
                                      "last": clock(datetime.fromisoformat(sent[-1]["at"]), live.svc.tz)}
                        if sent else None})
        return out

    def summary_of(case: Case) -> dict:
        """The summary written when the clinician was asked (the model's, once it is ready), or from the facts."""
        return case.chart_summary or Drafter.template_summary(case, case.attestation_requested or [])

    @app.get("/api/attestations/{case_id}")
    def attestation(case_id: str, who: Principal = Depends(role(Role.CLINICIAN))):
        case = load(case_id)
        if case.ordering_clinician["id"] != who.clinician_id:
            raise HTTPException(403, "Only the ordering clinician can see this request.")
        viewed(who, "attestation.viewed", case_id)
        return live.presenter.attestation(case, summary_of(case), now())

    @app.post("/api/attestations/{case_id}")
    def sign(case_id: str, body: AttestBody, who: Principal = Depends(role(Role.CLINICIAN))):
        case = live.svc.workflow.sign_attestation(who, load(case_id).id, body.answers)
        out = live.presenter.attestation(case, NO_SUMMARY, now())
        out["undo_until"] = case.last_action.undo_until.isoformat() if case.last_action else None
        out["answers"] = body.answers
        return out

    @app.post("/api/attestations/{case_id}/note")
    def sign_note(case_id: str, body: NoteBody, who: Principal = Depends(role(Role.CLINICIAN))):
        case = live.svc.workflow.sign_note(who, load(case_id).id, body.text)
        out = live.presenter.attestation(case, NO_SUMMARY, now())
        out["undo_until"] = case.last_action.undo_until.isoformat() if case.last_action else None
        return out

    @app.post("/api/attestations/{case_id}/decline-note")
    def decline_note(case_id: str, who: Principal = Depends(role(Role.CLINICIAN))):
        case = live.svc.workflow.decline_note(who, load(case_id).id)
        out = live.presenter.attestation(case, NO_SUMMARY, now())
        out["undo_until"] = case.last_action.undo_until.isoformat() if case.last_action else None
        return out

    ui = Path(os.environ.get("CADUCEUS_UI_DIST") or Path(__file__).resolve().parents[2] / "ui" / "dist")
    if ui.exists():
        app.mount("/assets", StaticFiles(directory=ui / "assets"), name="assets")

        @app.get("/{path:path}", include_in_schema=False)
        def spa(path: str):
            if path == "api" or path.startswith("api/"):  # an address the API does not have is not a page either
                raise HTTPException(404, "There is no such address in the API.")
            target = ui / path
            if path and target.is_file() and ui in target.resolve().parents:
                return FileResponse(target)
            return FileResponse(ui / "index.html")

    return app


def utcnow() -> datetime:
    return datetime.now(UTC)
