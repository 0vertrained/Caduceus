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
from typing import Annotated

import jwt
from fastapi import Depends, FastAPI, Header, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from caduceus.app_factory import Services, Settings, build_services
from caduceus.audit.log import TamperDetected
from caduceus.auth import AuthError, Principal, Role
from caduceus.cases import Case, Fax, StaleCase, State
from caduceus.intake import FaxReadingUnavailable
from caduceus.outcomes import outcomes
from caduceus.presenter import Presenter, moment
from caduceus.ratelimit import DemoLimits
from caduceus.seed import clone_demo_services, fresh_demo_services
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


DEMO_RESET_COOLDOWN = timedelta(minutes=1)


SANDBOX_ID = re.compile(r"[0-9a-f]{32}")
MAX_SANDBOXES = 60           # each is a few hundred kilobytes; the least recently used goes first
TEMPLATE_MAX_AGE = timedelta(hours=6)


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


def create_app(services: Services | None = None, dispatch_interval: float | None = 5.0,
               fresh_demo: Callable[[], Services] | None = None) -> FastAPI:
    """dispatch_interval=None (tests) starts no background threads: no outbox dispatcher, no demo resets."""
    if services is None:
        settings = Settings()
        services = fresh_demo_services(settings) if settings.auth_mode == "demo" else build_services(settings)
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

    app = FastAPI(title="Caduceus", version="0.1.0", lifespan=lifespan, docs_url=None, redoc_url=None)
    app.state.live = live

    signing_key = live.svc.settings.proxy_signing_key
    app.state.demo_limits = DemoLimits() if live.demo else None

    @app.middleware("http")
    async def choose_sandbox(request: Request, call_next):
        """Registered first, so it runs last: only signed requests within the limits reach a visitor's copy."""
        sandbox_id = request.headers.get("x-caduceus-sandbox")
        if not live.demo or not request.url.path.startswith("/api/") or request.url.path == "/api/health":
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
        if limits is not None and request.url.path != "/api/health":
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
        for k, v in SECURITY_HEADERS.items():
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

    def view(case: Case, who: Principal) -> dict:
        live.svc.audit.append(who.id, who.role, "case.viewed", case.id)
        return live.presenter.case(load(case.id), now(), who.id, read_only=who.role != Role.PA_SPECIALIST)

    @app.get("/api/health")
    def health() -> dict:
        return {"ok": True}

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

    @app.post("/api/cases/{case_id}/request-attestation")
    def request_attestation(case_id: str, who: Principal = Depends(principal)):
        return view(live.svc.workflow.request_attestation(who, load(case_id).id), who)

    @app.post("/api/cases/{case_id}/approve")
    def approve(case_id: str, who: Principal = Depends(principal)):
        return view(live.svc.workflow.approve(who, load(case_id).id), who)

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

    @app.post("/api/cases/{case_id}/undo")
    def undo(case_id: str, who: Principal = Depends(principal)):
        case = live.svc.workflow.undo(who, load(case_id).id)
        if who.role == Role.CLINICIAN:
            return live.presenter.attestation(case, live.svc.workflow.drafter.chart_summary(
                case, live.presenter.patient(case), case.attestation_requested), now())
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

    @app.get("/api/attestations")
    def my_attestations(who: Principal = Depends(role(Role.CLINICIAN))):
        mine = [c for c in live.svc.cases.all() if c.state in (State.AWAITING_ATTESTATION, State.AWAITING_NOTE)
                and c.ordering_clinician["id"] == who.clinician_id]
        return [{"case_id": c.id, "patient": live.presenter.row(c, now())["patient"], "service": c.service["short"],
                 "kind": "note" if c.state == State.AWAITING_NOTE else "attestation"}
                for c in sorted(mine, key=lambda c: c.due_at)]

    @app.get("/api/attestations/{case_id}")
    def attestation(case_id: str, who: Principal = Depends(role(Role.CLINICIAN))):
        case = load(case_id)
        if case.ordering_clinician["id"] != who.clinician_id:
            raise HTTPException(403, "Only the ordering clinician can see this request.")
        live.svc.audit.append(who.id, who.role, "attestation.viewed", case_id)
        summary = live.svc.workflow.drafter.chart_summary(case, live.presenter.patient(case),
                                                     case.attestation_requested or [])
        return live.presenter.attestation(case, summary, now())

    @app.post("/api/attestations/{case_id}")
    def sign(case_id: str, body: AttestBody, who: Principal = Depends(role(Role.CLINICIAN))):
        case = live.svc.workflow.sign_attestation(who, load(case_id).id, body.answers)
        out = live.presenter.attestation(case, "", now())
        out["undo_until"] = case.last_action.undo_until.isoformat() if case.last_action else None
        out["answers"] = body.answers
        return out

    @app.post("/api/attestations/{case_id}/note")
    def sign_note(case_id: str, body: NoteBody, who: Principal = Depends(role(Role.CLINICIAN))):
        case = live.svc.workflow.sign_note(who, load(case_id).id, body.text)
        out = live.presenter.attestation(case, "", now())
        out["undo_until"] = case.last_action.undo_until.isoformat() if case.last_action else None
        return out

    @app.post("/api/attestations/{case_id}/decline-note")
    def decline_note(case_id: str, who: Principal = Depends(role(Role.CLINICIAN))):
        case = live.svc.workflow.decline_note(who, load(case_id).id)
        out = live.presenter.attestation(case, "", now())
        out["undo_until"] = case.last_action.undo_until.isoformat() if case.last_action else None
        return out

    ui = Path(os.environ.get("CADUCEUS_UI_DIST") or Path(__file__).resolve().parents[2] / "ui" / "dist")
    if ui.exists():
        app.mount("/assets", StaticFiles(directory=ui / "assets"), name="assets")

        @app.get("/{path:path}", include_in_schema=False)
        def spa(path: str):
            target = ui / path
            if path and target.is_file() and ui in target.resolve().parents:
                return FileResponse(target)
            return FileResponse(ui / "index.html")

    return app


def utcnow() -> datetime:
    return datetime.now(UTC)
