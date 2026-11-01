"""Builds the HTTP API: the services it serves and its background work, its middleware and routers, and the built UI
where the API serves that too."""

from __future__ import annotations

import os
import threading
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from functools import partial
from pathlib import Path

import httpx
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from caduceus.api.headers import security_headers
from caduceus.api.middleware import (
    DemoLimitsMiddleware,
    ProxySignatureMiddleware,
    SandboxMiddleware,
    SecurityHeadersMiddleware,
)
from caduceus.api.routers import (
    admin,
    attestations,
    cases,
    demo,
    intake,
    notes_and_appeals,
    reports,
    session,
    smart_launch,
)
from caduceus.api.sandboxes import Live
from caduceus.api.views import VIEW_AGAIN, dispatch_due
from caduceus.app_factory import Services, Settings, build_services
from caduceus.cases import StaleCase
from caduceus.ratelimit import DemoLimits
from caduceus.seed import fresh_demo_services
from caduceus.smart import Limits, SmartLaunch, lookback
from caduceus.tools import ToolForbidden, ToolNotAllowed
from caduceus.workflow import ActionRefused

ROUTERS = (session.router, cases.router, notes_and_appeals.router, attestations.router, intake.router,
           smart_launch.router, reports.router, admin.router, demo.router)
UI_DIST = Path(__file__).resolve().parents[3] / "ui" / "dist"


async def action_refused(_: Request, e: ActionRefused) -> JSONResponse:
    return JSONResponse({"message": str(e)} | e.detail, status_code=409)


async def stale_case(_: Request, e: StaleCase) -> JSONResponse:
    return JSONResponse({"message": "Someone else changed this case. It has been reloaded."}, status_code=409)


async def tool_refused(_: Request, e: Exception) -> JSONResponse:
    return JSONResponse({"message": "That action is not allowed here."}, status_code=403)


def run_dispatcher(live: Live, interval: float, stop: threading.Event) -> None:
    while not stop.wait(interval):
        for sandbox in live.all():
            dispatch_due(sandbox.svc)


def run_demo_resets(live: Live, stop: threading.Event) -> None:
    while not stop.wait(300):
        live.expire()


def spa(path: str, request: Request) -> FileResponse:
    """A file of the built UI, or its page for any other address, for the browser's router to show."""
    if path == "api" or path.startswith("api/"):  # an address the API does not have is not a page either
        raise HTTPException(404, "There is no such address in the API.")
    ui: Path = request.app.state.ui
    target = ui / path
    if path and target.is_file() and ui in target.resolve().parents:  # never a file outside the build
        return FileResponse(target)
    return FileResponse(ui / "index.html")


def create_app(services: Services | None = None, dispatch_interval: float | None = 5.0,
               fresh_demo: Callable[[], Services] | None = None, ehr_transport: httpx.BaseTransport | None = None,
               ehr_limits: Limits | None = None) -> FastAPI:
    """dispatch_interval=None (tests) starts no background threads; ehr_transport stands in for the EHR's network."""
    built: Services | None = None  # what this app builds, it closes when it stops; services handed in stay open
    if services is None:
        settings = Settings()
        services = built = fresh_demo_services(settings) if settings.auth_mode == "demo" else build_services(settings)
    settings = services.settings
    if fresh_demo is None and settings.auth_mode == "demo":
        fresh_demo = partial(fresh_demo_services, settings)
    live = Live(services, fresh_demo)
    stop = threading.Event()

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        if dispatch_interval:
            threading.Thread(target=run_dispatcher, args=(live, dispatch_interval, stop), name="outbox",
                             daemon=True).start()
            if live.demo:
                threading.Thread(target=run_demo_resets, args=(live, stop), name="demo-reset", daemon=True).start()
        yield
        stop.set()
        if built is not None:
            built.close()  # hands the Postgres connections back; nothing to do on SQLite

    app = FastAPI(title="Caduceus", version="0.1.0", lifespan=lifespan, docs_url=None, redoc_url=None,
                  exception_handlers={ActionRefused: action_refused, StaleCase: stale_case,
                                      ToolForbidden: tool_refused, ToolNotAllowed: tool_refused})
    app.state.live = live
    app.state.demo_limits = DemoLimits() if live.demo else None
    app.state.smart = SmartLaunch(settings.smart_allowed, settings.smart_redirects, settings.smart_client_id,
                                  transport=ehr_transport, limits=ehr_limits)
    # How far back a record opened from an EHR is read: the criteria's own windows.
    app.state.lookback = lookback(services.workflow.in_use())
    app.state.view_again = VIEW_AGAIN

    # Starlette runs the middleware added last first: every response gets the security headers, refusals included,
    # only requests Netlify signed count toward the demo's limits, and only those within the limits reach a
    # visitor's copy of the cases.
    app.add_middleware(SandboxMiddleware)
    app.add_middleware(DemoLimitsMiddleware)
    app.add_middleware(ProxySignatureMiddleware, signing_key=settings.proxy_signing_key)
    app.add_middleware(SecurityHeadersMiddleware, headers=security_headers(settings))

    for router in ROUTERS:
        app.include_router(router, prefix="/api")
    ui = Path(os.environ.get("CADUCEUS_UI_DIST") or UI_DIST)
    if ui.exists():
        app.state.ui = ui
        app.mount("/assets", StaticFiles(directory=ui / "assets"), name="assets")
        app.add_api_route("/{path:path}", spa, methods=["GET"], include_in_schema=False)
    return app
