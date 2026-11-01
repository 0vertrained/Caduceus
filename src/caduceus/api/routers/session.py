"""The health check, how the browser signs in, and who is signed in."""

from __future__ import annotations

from fastapi import APIRouter

from caduceus.api.dependencies import LiveDep, SignedIn

router = APIRouter()


@router.get("/health")
def health() -> dict:
    return {"ok": True}


@router.get("/auth-config")
def auth_config(live: LiveDep) -> dict:
    """How the browser signs in, for anyone to read: the mode, and with OIDC what the UI's public client needs to
    start Authorization Code with PKCE. Nothing secret is here, and nothing secret is needed: the UI holds none."""
    s = live.svc.settings
    if s.auth_mode != "oidc":
        return {"mode": s.auth_mode}
    return {"mode": "oidc", "issuer": s.oidc_issuer, "client_id": s.oidc_client_id, "scope": s.oidc_scope}


@router.get("/me")
def me(who: SignedIn) -> dict:
    return {"id": who.id, "name": who.name, "role": who.role.value}
