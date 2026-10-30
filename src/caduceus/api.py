"""The HTTP API the review UI talks to. Every response that can carry PHI is marked no-store."""

from __future__ import annotations

import os
import threading
from collections.abc import Callable
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path

from fastapi import Depends, FastAPI, Header, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from caduceus.app_factory import Services, Settings, build_services
from caduceus.audit.log import TamperDetected
from caduceus.auth import AuthError, Principal, Role
from caduceus.cases import Case, StaleCase, State
from caduceus.presenter import Presenter
from caduceus.seed import fresh_demo_services
from caduceus.tools import ToolForbidden, ToolNotAllowed
from caduceus.workflow import ActionRefused

SECURITY_HEADERS = {
    "Content-Security-Policy": "default-src 'self'; img-src 'self' data:; style-src 'self'; font-src 'self'; "
                               "frame-ancestors 'none'; base-uri 'none'; form-action 'self'",
    "X-Content-Type-Options": "nosniff",
    "Referrer-Policy": "no-referrer",
    "Strict-Transport-Security": "max-age=31536000; includeSubDomains",
    "Permissions-Policy": "camera=(), microphone=(), geolocation=()",
    "Cross-Origin-Opener-Policy": "same-origin",
}


DEMO_RESET_COOLDOWN = timedelta(minutes=1)


class Live:
    """The services the API is serving right now. In demo mode a reset swaps in a freshly seeded set, so the
    public demo always comes back to a clean queue; handlers read through this holder rather than keep a copy."""

    def __init__(self, svc: Services, fresh: Callable[[], Services] | None):
        self._lock = threading.Lock()
        self.fresh = fresh
        self.demo = svc.settings.auth_mode == "demo"
        self.reset_every = timedelta(hours=float(svc.settings.demo_reset_hours)) if self.demo else None
        self._install(svc)

    def _install(self, svc: Services) -> None:
        self.svc = svc
        self.presenter = Presenter(svc.store, svc.workflow.library, svc.tz)
        self.last_reset = svc.workflow.now()

    def reset(self) -> None:
        if self.fresh is None:
            raise RuntimeError("only the demo can be reset")
        fresh = self.fresh()  # built and seeded before the swap, so requests never see a half-made set
        with self._lock:
            self._install(fresh)


class ReturnBody(BaseModel):
    reason: str


class AttestBody(BaseModel):
    answers: dict[str, str]


class DraftBody(BaseModel):
    text: str


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

    def safe_dispatch() -> None:
        """Reading a case must never fail because sending another one did; failures are audited instead."""
        try:
            live.svc.workflow.dispatch_due()
        except Exception as e:  # noqa: BLE001
            try:
                live.svc.audit.append("caduceus-agent", "agent_runtime", "dispatch.error", None,
                                      {"error": type(e).__name__})
            except Exception:  # noqa: BLE001, S110 - the audit store itself is unavailable; nothing safer to do
                pass

    def dispatcher() -> None:
        while not stop.wait(dispatch_interval or 5.0):
            safe_dispatch()

    def demo_resetter() -> None:
        while not stop.wait(live.reset_every.total_seconds()):  # type: ignore[union-attr]
            live.reset()

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
        return JSONResponse({"message": str(e)}, status_code=409)

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
        return live.presenter.case(load(case.id), now(), who.id)

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
                "next_reset_at": (live.last_reset + live.reset_every).isoformat()}  # type: ignore[operator]

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
    def queue(who: Principal = Depends(role(Role.PA_SPECIALIST, Role.ADMIN))) -> dict:
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
                case, live.presenter.patient(case), case.attestation_requested))
        return view(case, who)

    @app.get("/api/cases/{case_id}/history")
    def history(case_id: str, who: Principal = Depends(role(Role.PA_SPECIALIST, Role.ADMIN, Role.AUDITOR))):
        load(case_id)
        return [{"seq": e.seq, "at": e.ts, "actor": e.actor, "role": e.role, "action": e.action, "detail": e.detail}
                for e in live.svc.audit.events(case_id)]

    @app.get("/api/attestations")
    def my_attestations(who: Principal = Depends(role(Role.CLINICIAN))):
        mine = [c for c in live.svc.cases.all() if c.state == State.AWAITING_ATTESTATION
                and c.ordering_clinician["id"] == who.clinician_id]
        return [{"case_id": c.id, "patient": live.presenter.row(c, now())["patient"], "service": c.service["short"]}
                for c in mine]

    @app.get("/api/attestations/{case_id}")
    def attestation(case_id: str, who: Principal = Depends(role(Role.CLINICIAN))):
        case = load(case_id)
        if case.ordering_clinician["id"] != who.clinician_id:
            raise HTTPException(403, "Only the ordering clinician can see this request.")
        live.svc.audit.append(who.id, who.role, "attestation.viewed", case_id)
        summary = live.svc.workflow.drafter.chart_summary(case, live.presenter.patient(case),
                                                     case.attestation_requested or [])
        return live.presenter.attestation(case, summary)

    @app.post("/api/attestations/{case_id}")
    def sign(case_id: str, body: AttestBody, who: Principal = Depends(role(Role.CLINICIAN))):
        case = live.svc.workflow.sign_attestation(who, load(case_id).id, body.answers)
        out = live.presenter.attestation(case, "")
        out["undo_until"] = case.last_action.undo_until.isoformat() if case.last_action else None
        out["answers"] = body.answers
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
