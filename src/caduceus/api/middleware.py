"""What every request passes through: the security headers, Netlify's signature, the demo's request limits, and the
choice of the visitor's own copy of the cases."""

from __future__ import annotations

import jwt
from fastapi import Request
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.responses import Response
from starlette.types import ASGIApp

from caduceus.api.dependencies import visitor_of
from caduceus.api.sandboxes import SANDBOX_HEADER, SANDBOX_ID, TooManySandboxes

# Neither reads any data, and every page load asks the second before anything else, so the demo's limits count
# neither, and neither starts a visitor's copy of the cases.
NOT_COUNTED = {"/api/health", "/api/auth-config"}


class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    """The security headers on every response, and no-store on the API's, which can carry PHI."""

    def __init__(self, app: ASGIApp, headers: dict[str, str]):
        super().__init__(app)
        self.headers = headers

    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        response = await call_next(request)
        for k, v in self.headers.items():
            response.headers.setdefault(k, v)
        if request.url.path.startswith("/api/"):
            response.headers["Cache-Control"] = "no-store"
        return response


class ProxySignatureMiddleware(BaseHTTPMiddleware):
    """Behind Netlify, answer only what Netlify proxied: it signs each request with the shared key (HS256 JWS in
    x-nf-sign)."""

    def __init__(self, app: ASGIApp, signing_key: str | None):
        super().__init__(app)
        self.signing_key = signing_key

    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        if self.signing_key and request.url.path != "/api/health":
            try:
                jwt.decode(request.headers.get("x-nf-sign", ""), self.signing_key, algorithms=["HS256"],
                           issuer="netlify", leeway=30, options={"require": ["exp", "iss"]})
            except jwt.PyJWTError:
                return JSONResponse({"message": "This API answers only through the Caduceus demo site."},
                                    status_code=403)
        return await call_next(request)


class DemoLimitsMiddleware(BaseHTTPMiddleware):
    """The demo's request limits, on the API's requests only: the demo's pages and their files are Netlify's to
    serve, so here they come only from a walk on one machine."""

    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        limits = request.app.state.demo_limits
        path = request.url.path
        if limits is not None and path.startswith("/api/") and path not in NOT_COUNTED:
            visitor = visitor_of(request)
            wait = limits.check(visitor, write=request.method in ("POST", "PUT", "PATCH", "DELETE"))
            if wait:
                return JSONResponse({"message": "Too many requests from your address. Wait a few minutes and "
                                                "try again."}, status_code=429, headers={"Retry-After": str(wait)})
        return await call_next(request)


class SandboxMiddleware(BaseHTTPMiddleware):
    """In the demo, the visitor's own copy of the cases for the rest of the request."""

    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        live = request.app.state.live
        sandbox_id = request.headers.get(SANDBOX_HEADER)
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
