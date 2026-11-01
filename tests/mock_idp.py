"""A mock OpenID Connect provider. FOR TESTS ONLY: it signs in anyone who clicks a name, with no password.

It listens on 127.0.0.1 only, lives outside the caduceus package and the image, and refuses to be imported except by
pytest or run as a script. It checks what a real provider checks of a public client (the exact redirect URI, PKCE S256,
state and nonce, one-time codes, rotating refresh tokens, a confirmed sign-out), so the tests that use it show
something. Tokens are RS256 JWTs for the development roster, and the endpoints sit at Keycloak's paths.

    python -m tests.mock_idp --port 8771 --redirect-uri http://127.0.0.1:8766/auth/callback \\
        --post-logout-redirect-uri http://127.0.0.1:8766/signed-out
"""

from __future__ import annotations

import argparse
import base64
import contextlib
import hashlib
import hmac
import html
import json
import re
import secrets
import sys
import threading
import time
from collections.abc import Iterable
from dataclasses import dataclass
from http.cookies import CookieError, SimpleCookie
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any
from urllib.parse import parse_qs, urlencode, urlsplit

import jwt
from cryptography.hazmat.primitives.asymmetric import rsa
from jwt.algorithms import RSAAlgorithm

from caduceus.auth import DEV_USERS, Role

if __name__ != "__main__" and "pytest" not in sys.modules:
    raise ImportError("tests.mock_idp signs anyone in without a password. Run it as a script for the end-to-end walk, "
                      "or use it from pytest; nothing else may import it.")

ROLE_WORDS = {Role.PA_SPECIALIST: "Prior authorization specialist", Role.CLINICIAN: "Ordering clinician",
              Role.ADMIN: "Administrator", Role.AUDITOR: "Auditor"}
OIDC = "/protocol/openid-connect"
CODE_SECONDS = 60
VERIFIER = re.compile(r"[A-Za-z0-9._~-]{43,128}")  # RFC 7636 section 4.1
CHALLENGE = re.compile(r"[A-Za-z0-9_-]{43}")  # BASE64URL(SHA-256(verifier)), without padding
COOKIE = "mock_idp_session"
MAX_BODY = 64 * 1024
PAGE_POLICY = "default-src 'none'; style-src 'unsafe-inline'; frame-ancestors 'none'; base-uri 'none'"
STYLE = ("body{margin:0;font:18px/1.5 system-ui,sans-serif;color:#1a2330;background:#eef1f3}"
         "main{max-width:34rem;margin:0 auto;padding:32px 16px;display:grid;gap:16px}h1{font-size:28px;margin:0}"
         "p{margin:0}.test{background:#fbf6ea;color:#7a4e00;font-weight:600;padding:8px 12px;border-radius:6px}"
         "ul{list-style:none;margin:0;padding:0;display:grid;gap:12px}"
         "button{width:100%;min-height:54px;padding:10px 16px;font:inherit;font-weight:600;text-align:left;"
         "display:flex;flex-wrap:wrap;justify-content:space-between;gap:4px 12px;background:#fff;color:#1a2330;"
         "border:1px solid #1a2330;border-radius:6px;cursor:pointer}button span{font-weight:400;color:#4a5560}"
         "button:focus-visible{outline:3px solid #1a2330;outline-offset:3px}")


def s256(verifier: str) -> str:
    """The PKCE S256 challenge for a verifier (RFC 7636 section 4.2)."""
    return base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()


@dataclass(frozen=True)
class Client:
    """The one client this provider knows. It is public, so it has no secret."""

    client_id: str
    redirect_uris: tuple[str, ...]
    post_logout_redirect_uris: tuple[str, ...] = ()

    @property
    def origins(self) -> set[str]:
        """Where its pages come from: the only origins the token endpoint answers from another origin."""
        return {f"{u.scheme}://{u.netloc}" for u in map(urlsplit, self.redirect_uris)}


class Refused(Exception):
    """An OAuth error: JSON from the token endpoint, a page wherever a person is looking."""

    def __init__(self, error: str, description: str, status: int = 400):
        super().__init__(description)
        self.error, self.description, self.status = error, description, status


def one(params: dict[str, list[str]]) -> dict[str, str]:
    """Each parameter once (RFC 6749 section 3.1): a repeated one could mean two things to two readers."""
    if any(len(v) > 1 for v in params.values()):
        raise Refused("invalid_request", "A parameter is repeated.")
    return {k: v[0] for k, v in params.items()}


class MockIdP:
    def __init__(self, client: Client, audience: str, port: int = 0, path: str = "/realms/mesaverde",
                 access_seconds: int = 300, refresh_seconds: int = 1800, log: bool = False):
        self.client, self.audience, self.path, self.log = client, audience, path.rstrip("/"), log
        self.access_seconds, self.refresh_seconds = access_seconds, refresh_seconds
        self.key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        self.kid = secrets.token_hex(8)
        self._lock = threading.Lock()
        self._pending: dict[str, dict] = {}  # authorization requests waiting for a person to choose
        self._codes: dict[str, dict] = {}
        self._used_codes: dict[str, str] = {}  # every code exchanged, and the grant it started
        self._sessions: dict[str, dict] = {}  # session id: who signed in, and when
        self._refresh: dict[str, dict] = {}
        self._rotated: dict[str, str] = {}  # refresh tokens already exchanged, and their grant
        self.server = ThreadingHTTPServer(("127.0.0.1", port), _handler(self))
        self.issuer = f"http://127.0.0.1:{self.server.server_port}{self.path}"

    # For tests --------------------------------------------------------------------------------------------------------

    def start(self) -> MockIdP:
        threading.Thread(target=self.server.serve_forever, name="mock-idp", daemon=True).start()
        return self

    def stop(self) -> None:
        self.server.shutdown()
        self.server.server_close()

    def sign(self, claims: dict[str, Any], key: Any = None, typ: str = "JWT", algorithm: str = "RS256") -> str:
        return jwt.encode(claims, key or self.key, algorithm=algorithm, headers={"kid": self.kid, "typ": typ})

    def access_token(self, persona: str, *, sid: str | None = None, scope: str = "openid profile",
                     audience: str | None = None, issuer: str | None = None, expires_in: int | None = None,
                     drop: Iterable[str] = (), claims: dict[str, Any] | None = None, key: Any = None,
                     algorithm: str = "RS256") -> str:
        """An access token as the token endpoint issues them. A test changes one thing to see the API refuse it."""
        p, now = DEV_USERS[persona], int(time.time())
        body: dict[str, Any] = {
            "iss": issuer or self.issuer, "aud": audience or self.audience, "sub": p.id, "azp": self.client.client_id,
            "exp": now + (self.access_seconds if expires_in is None else expires_in), "iat": now,
            "jti": secrets.token_hex(8), "scope": scope, "name": p.name, "caduceus_role": p.role.value}
        if p.clinician_id:
            body["clinician_id"] = p.clinician_id
        if sid:
            body["sid"] = sid
        body |= claims or {}
        for name in drop:
            body.pop(name, None)
        return self.sign(body, key=key, typ="at+jwt", algorithm=algorithm)

    # The endpoints ----------------------------------------------------------------------------------------------------

    def discovery(self) -> dict[str, Any]:
        base = self.issuer + OIDC
        return {"issuer": self.issuer, "authorization_endpoint": f"{base}/auth", "token_endpoint": f"{base}/token",
                "jwks_uri": f"{base}/certs", "end_session_endpoint": f"{base}/logout",
                "response_types_supported": ["code"], "response_modes_supported": ["query", "fragment"],
                "grant_types_supported": ["authorization_code", "refresh_token"],
                "subject_types_supported": ["public"], "id_token_signing_alg_values_supported": ["RS256"],
                "code_challenge_methods_supported": ["S256"], "token_endpoint_auth_methods_supported": ["none"],
                "scopes_supported": ["openid", "profile"],
                "claims_supported": ["sub", "name", "caduceus_role", "clinician_id"]}

    def jwks(self) -> dict[str, Any]:
        jwk = json.loads(RSAAlgorithm.to_jwk(self.key.public_key()))
        return {"keys": [jwk | {"kid": self.kid, "alg": "RS256", "use": "sig"}]}

    def handle(self, h: BaseHTTPRequestHandler, method: str) -> None:
        url = urlsplit(h.path)
        route = url.path[len(self.path):] if url.path.startswith(self.path + "/") else None
        try:
            if method == "POST":
                length = int(h.headers.get("Content-Length") or 0)
                if length > MAX_BODY:
                    raise Refused("invalid_request", "The request is too large.", 413)
                params = one(parse_qs(h.rfile.read(length).decode(), keep_blank_values=True))
            else:
                params = one(parse_qs(url.query, keep_blank_values=True))
            match method, route:
                case "GET", "/.well-known/openid-configuration":
                    self._json(h, self.discovery(), cors="*")
                case "GET", "/protocol/openid-connect/certs":
                    self._json(h, self.jwks(), cors="*")
                case "GET", "/protocol/openid-connect/auth":
                    self.authorize(h, params)
                case "POST", "/protocol/openid-connect/auth":
                    self.choose(h, params)
                case "OPTIONS", "/protocol/openid-connect/token":
                    self.preflight(h)
                case "POST", "/protocol/openid-connect/token":
                    self.token(h, params)
                case ("GET" | "POST"), "/protocol/openid-connect/logout":
                    self.end_session(h, method, params)
                case _:
                    self._page(h, 404, "Not found", "There is nothing at this address.")
        except Refused as e:
            if route == f"{OIDC}/token":
                self._json(h, {"error": e.error, "error_description": e.description}, status=e.status,
                           cors=self._cors(h))
            else:
                self._page(h, e.status, "This request was refused", e.description)

    def authorize(self, h: BaseHTTPRequestHandler, q: dict[str, str]) -> None:
        # A wrong client or redirect URI is told to the person and sent nowhere (RFC 6749 section 4.1.2.1).
        if q.get("client_id") != self.client.client_id:
            self._page(h, 400, "Unknown client", "This sign-in request names a client this provider does "
                                                 "not know.")
            return
        redirect_uri = q.get("redirect_uri", "")
        if redirect_uri not in self.client.redirect_uris:
            self._page(h, 400, "Unregistered address", "This sign-in request asks to return to an address "
                                                       "that is not registered for its client.")
            return
        mode = q.get("response_mode", "query")
        if mode not in ("query", "fragment"):
            self._page(h, 400, "Unsupported response mode", "Ask for the query or the fragment.")
            return

        def back(**params: str) -> None:
            self._redirect(h, redirect_uri, mode, params | ({"state": q["state"]} if q.get("state") else {}))

        if q.get("response_type") != "code":
            back(error="unsupported_response_type", error_description="Only the code flow is offered.")
            return
        if "openid" not in q.get("scope", "").split():
            back(error="invalid_scope", error_description="The openid scope is required.")
            return
        if q.get("code_challenge_method") != "S256" or not CHALLENGE.fullmatch(q.get("code_challenge", "")):
            back(error="invalid_request", error_description="PKCE with S256 is required.")
            return
        if not q.get("state") or not q.get("nonce"):
            back(error="invalid_request", error_description="state and nonce are required.")
            return
        sid = self._live_session(h)
        if sid and q.get("prompt") != "login":
            back(code=self._code(q, sid))  # signed in here already: no need to choose again
            return
        if q.get("prompt") == "none":
            back(error="login_required", error_description="No one is signed in.")
            return
        request_id = secrets.token_urlsafe(16)
        with self._lock:
            self._pending[request_id] = q | {"expires": str(time.time() + 600)}
        people = "".join(
            f'<li><button type="submit" name="persona" value="{html.escape(key)}">{html.escape(p.name)} '
            f"<span>{html.escape(ROLE_WORDS[p.role])}</span></button></li>" for key, p in DEV_USERS.items())
        self._page(h, 200, "Sign in to Mesa Verde", "Choose who to sign in as.",
                   f'<form method="post" action="{self.path}{OIDC}/auth"><input type="hidden" name="request" '
                   f'value="{request_id}"><ul>{people}</ul></form>')

    def choose(self, h: BaseHTTPRequestHandler, form: dict[str, str]) -> None:
        with self._lock:
            q = self._pending.pop(form.get("request", ""), None)
        if q is None or float(q["expires"]) < time.time():
            self._page(h, 400, "This sign-in has expired", "Go back to Caduceus and sign in again.")
            return
        if form.get("persona") not in DEV_USERS:
            self._page(h, 400, "No such person", "Go back to Caduceus and sign in again.")
            return
        sid = secrets.token_urlsafe(16)
        with self._lock:
            self._sessions[sid] = {"persona": form["persona"], "auth_time": int(time.time())}
        self._redirect(h, q["redirect_uri"], q.get("response_mode", "query"),
                       {"code": self._code(q, sid), "state": q["state"]},
                       cookie=f"{COOKIE}={sid}; Path={self.path}; HttpOnly; SameSite=Lax")

    def token(self, h: BaseHTTPRequestHandler, form: dict[str, str]) -> None:
        if form.get("client_id") != self.client.client_id or "client_secret" in form:
            raise Refused("invalid_client", "This is a public client: its id, and no secret.", 401)
        match form.get("grant_type"):
            case "authorization_code":
                body = self._exchange_code(form)
            case "refresh_token":
                body = self._exchange_refresh(form)
            case _:
                raise Refused("unsupported_grant_type", "Only authorization_code and refresh_token are offered.")
        self._json(h, body, cors=self._cors(h))

    def preflight(self, h: BaseHTTPRequestHandler) -> None:
        h.send_response(204)
        if origin := self._cors(h):
            h.send_header("Access-Control-Allow-Origin", origin)
            h.send_header("Access-Control-Allow-Methods", "POST")
            h.send_header("Access-Control-Allow-Headers", "content-type")
            h.send_header("Access-Control-Max-Age", "600")
        h.send_header("Vary", "Origin")
        h.send_header("Content-Length", "0")
        h.end_headers()

    def end_session(self, h: BaseHTTPRequestHandler, method: str, q: dict[str, str]) -> None:
        hint = q.get("id_token_hint")
        if hint:
            try:
                jwt.decode(hint, self.key.public_key(), algorithms=["RS256"], audience=self.client.client_id,
                           issuer=self.issuer, options={"verify_exp": False})
            except jwt.PyJWTError:
                self._page(h, 400, "Sign-out refused", "The ID token given is not one this provider issued.")
                return
        elif q.get("client_id") != self.client.client_id:
            self._page(h, 400, "Sign-out refused", "The request names no client this provider knows.")
            return
        back = q.get("post_logout_redirect_uri")
        if back and back not in self.client.post_logout_redirect_uris:
            self._page(h, 400, "Sign-out refused", "It asks to return to an address that is not registered "
                                                   "for its client.")
            return
        if method == "GET" and not hint:
            # As Keycloak does without an ID token: ask first, so a link alone cannot sign someone out.
            fields = "".join(f'<input type="hidden" name="{k}" value="{html.escape(v)}">' for k, v in q.items()
                             if k in ("client_id", "post_logout_redirect_uri", "state"))
            self._page(h, 200, "Sign out of Mesa Verde?", "You will be signed out of everything that uses "
                                                          "your Mesa Verde account.",
                       f'<form method="post" action="{self.path}{OIDC}/logout">{fields}'
                       "<button type=\"submit\">Sign out</button></form>")
            return
        sid = self._session_id(h)
        with self._lock:
            self._sessions.pop(sid or "", None)
            for token in [t for t, grant in self._refresh.items() if grant["sid"] == sid]:
                del self._refresh[token]
        cookie = f"{COOKIE}=; Path={self.path}; HttpOnly; SameSite=Lax; Max-Age=0"
        if back:
            self._redirect(h, back, "query", {"state": q["state"]} if q.get("state") else {}, cookie=cookie)
            return
        self._page(h, 200, "You are signed out", "You are signed out of Mesa Verde.", cookie=cookie)

    # Codes, grants and tokens -----------------------------------------------------------------------------------------

    def _code(self, q: dict[str, str], sid: str) -> str:
        code = secrets.token_urlsafe(32)
        with self._lock:
            self._codes[code] = {"redirect_uri": q["redirect_uri"], "challenge": q["code_challenge"],
                                 "nonce": q.get("nonce"), "scope": q["scope"], "sid": sid,
                                 "expires": time.time() + CODE_SECONDS}
        return code

    def _exchange_code(self, form: dict[str, str]) -> dict[str, Any]:
        code = form.get("code", "")
        with self._lock:
            grant = self._codes.pop(code, None)  # spent now, whatever follows: a code is never tried twice
            if grant is None:
                if family := self._used_codes.get(code):
                    self._end_grant(family)  # a code used twice: what it gave is revoked (RFC 6749 section 4.1.2)
                raise Refused("invalid_grant", "The code is unknown, or was used already.")
            family = self._used_codes[code] = secrets.token_hex(8)
        if grant["expires"] < time.time():
            raise Refused("invalid_grant", "The code expired.")
        if form.get("redirect_uri") != grant["redirect_uri"]:
            raise Refused("invalid_grant", "redirect_uri is not the one the code was issued for.")
        verifier = form.get("code_verifier", "")
        if not VERIFIER.fullmatch(verifier) or not hmac.compare_digest(s256(verifier), grant["challenge"]):
            raise Refused("invalid_grant", "The PKCE code_verifier does not match the code_challenge.")
        return self._issue(grant["sid"], grant["scope"], family, nonce=grant["nonce"])

    def _exchange_refresh(self, form: dict[str, str]) -> dict[str, Any]:
        token = form.get("refresh_token", "")
        with self._lock:
            grant = self._refresh.pop(token, None)
            if grant is None:
                if family := self._rotated.get(token):
                    self._end_grant(family)  # an old one used again: copied, or replayed; the whole grant ends
                raise Refused("invalid_grant", "The refresh token is unknown, used already, or revoked.")
            self._rotated[token] = grant["family"]
        if grant["expires"] < time.time():
            raise Refused("invalid_grant", "The refresh token expired.")
        return self._issue(grant["sid"], grant["scope"], grant["family"])

    def _end_grant(self, family: str) -> None:
        for token in [t for t, grant in self._refresh.items() if grant["family"] == family]:
            del self._refresh[token]

    def _issue(self, sid: str, scope: str, family: str, nonce: str | None = None) -> dict[str, Any]:
        with self._lock:
            session = self._sessions.get(sid)
        if session is None:
            raise Refused("invalid_grant", "The session has ended.")
        persona, now = session["persona"], int(time.time())
        p = DEV_USERS[persona]
        # The original sign-in's time and party stay the same through every refresh, as OIDC asks (section 12.2).
        id_token = {"iss": self.issuer, "sub": p.id, "aud": self.client.client_id, "azp": self.client.client_id,
                    "exp": now + self.access_seconds, "iat": now, "auth_time": session["auth_time"], "sid": sid,
                    "name": p.name}
        if nonce:
            id_token["nonce"] = nonce
        refresh = secrets.token_urlsafe(32)
        with self._lock:
            self._refresh[refresh] = {"sid": sid, "family": family, "scope": scope,
                                      "expires": now + self.refresh_seconds}
        return {"access_token": self.access_token(persona, sid=sid, scope=scope), "token_type": "Bearer",
                "expires_in": self.access_seconds, "id_token": self.sign(id_token), "refresh_token": refresh,
                "scope": scope}

    # HTTP -------------------------------------------------------------------------------------------------------------

    def _session_id(self, h: BaseHTTPRequestHandler) -> str | None:
        try:
            cookie = SimpleCookie(h.headers.get("Cookie", ""))
        except CookieError:
            return None
        return cookie[COOKIE].value if COOKIE in cookie else None

    def _live_session(self, h: BaseHTTPRequestHandler) -> str | None:
        sid = self._session_id(h)
        with self._lock:
            return sid if sid in self._sessions else None

    def _cors(self, h: BaseHTTPRequestHandler) -> str | None:
        origin = h.headers.get("Origin")
        return origin if origin in self.client.origins else None

    def _redirect(self, h: BaseHTTPRequestHandler, to: str, mode: str, params: dict[str, str],
                  cookie: str | None = None) -> None:
        separator = "#" if mode == "fragment" else "&" if "?" in to else "?"
        h.send_response(302)
        h.send_header("Location", f"{to}{separator}{urlencode(params)}" if params else to)
        h.send_header("Cache-Control", "no-store")
        h.send_header("Referrer-Policy", "no-referrer")
        if cookie:
            h.send_header("Set-Cookie", cookie)
        h.send_header("Content-Length", "0")
        h.end_headers()

    def _json(self, h: BaseHTTPRequestHandler, body: dict[str, Any], status: int = 200,
              cors: str | None = None) -> None:
        data = json.dumps(body).encode()
        h.send_response(status)
        h.send_header("Content-Type", "application/json")
        h.send_header("Cache-Control", "no-store")
        h.send_header("Pragma", "no-cache")
        if cors:
            h.send_header("Access-Control-Allow-Origin", cors)
        h.send_header("Vary", "Origin")
        h.send_header("Content-Length", str(len(data)))
        h.end_headers()
        h.wfile.write(data)

    def _page(self, h: BaseHTTPRequestHandler, status: int, title: str, text: str = "", body: str = "",
              cookie: str | None = None) -> None:
        data = (f'<!doctype html><html lang="en"><head><meta charset="utf-8">'
                f'<meta name="viewport" content="width=device-width, initial-scale=1">'
                f"<title>{html.escape(title)} – test identity provider</title><style>{STYLE}</style></head>"
                f'<body><main><p class="test">Test identity provider, for automated tests only. It asks for no '
                f"password.</p><h1>{html.escape(title)}</h1>{f'<p>{html.escape(text)}</p>' if text else ''}{body}"
                f"</main></body></html>").encode()
        h.send_response(status)
        h.send_header("Content-Type", "text/html; charset=utf-8")
        h.send_header("Content-Security-Policy", PAGE_POLICY)
        h.send_header("Cache-Control", "no-store")
        h.send_header("Referrer-Policy", "no-referrer")
        if cookie:
            h.send_header("Set-Cookie", cookie)
        h.send_header("Content-Length", str(len(data)))
        h.end_headers()
        h.wfile.write(data)


def _handler(idp: MockIdP) -> type[BaseHTTPRequestHandler]:
    class Handler(BaseHTTPRequestHandler):
        server_version, sys_version = "mock-idp", ""

        def do_GET(self) -> None:
            idp.handle(self, "GET")

        def do_POST(self) -> None:
            idp.handle(self, "POST")

        def do_OPTIONS(self) -> None:
            idp.handle(self, "OPTIONS")

        def log_request(self, code: int | str = "-", size: int | str = "-") -> None:
            # The path alone: a query carries codes, state and challenges, and none of that belongs in a log.
            if idp.log:
                sys.stderr.write(f"mock-idp: {self.command} {urlsplit(self.path).path} {code}\n")

        def log_message(self, format: str, *args: Any) -> None:
            pass  # http.server's own messages can quote the request line, query and all

    return Handler


def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser(description="A mock OpenID Connect provider, for tests only.")
    ap.add_argument("--port", type=int, default=8771)
    ap.add_argument("--path", default="/realms/mesaverde", help="the issuer's path")
    ap.add_argument("--client-id", default="caduceus-ui")
    ap.add_argument("--audience", default="caduceus-api", help="the API's audience, which access tokens carry")
    ap.add_argument("--redirect-uri", action="append", required=True, help="registered exactly; repeat for more")
    ap.add_argument("--post-logout-redirect-uri", action="append", default=[])
    ap.add_argument("--access-token-seconds", type=int, default=300)
    args = ap.parse_args(argv)
    idp = MockIdP(Client(args.client_id, tuple(args.redirect_uri), tuple(args.post_logout_redirect_uri)),
                  args.audience, port=args.port, path=args.path, access_seconds=args.access_token_seconds, log=True)
    print(f"Test identity provider (tests only) at {idp.issuer}", file=sys.stderr, flush=True)
    with contextlib.suppress(KeyboardInterrupt):
        idp.server.serve_forever()


if __name__ == "__main__":
    main()
