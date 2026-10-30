"""Who is acting, and in which role.

Development uses a fixed roster selected by the `X-Caduceus-User` header (so the demo needs no identity
provider). Deployment uses OIDC: a bearer JWT verified against the provider's JWKS, with the role taken from a
claim. Either way every request, tool call and audit event carries a Principal.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

import jwt


class Role(StrEnum):
    PA_SPECIALIST = "pa_specialist"
    CLINICIAN = "clinician"
    ADMIN = "admin"
    AUDITOR = "auditor"
    AGENT_RUNTIME = "agent_runtime"


@dataclass(frozen=True)
class Principal:
    id: str
    name: str
    role: Role
    clinician_id: str | None = None


AGENT = Principal("caduceus-agent", "Caduceus", Role.AGENT_RUNTIME)

DEV_USERS: dict[str, Principal] = {
    "dolores": Principal("dolores", "Dolores Montoya", Role.PA_SPECIALIST),
    "dr.reyes": Principal("dr.reyes", "Dr. Ana Reyes", Role.CLINICIAN, "pract-reyes"),
    "dr.lee": Principal("dr.lee", "Dr. Marcus Lee", Role.CLINICIAN, "pract-lee"),
    "dr.patel": Principal("dr.patel", "Dr. Priya Patel", Role.CLINICIAN, "pract-patel"),
    "avery": Principal("avery", "Avery Chen", Role.ADMIN),
    "sam": Principal("sam", "Sam Whitlock", Role.AUDITOR),
}


class AuthError(Exception):
    pass


class Authenticator:
    def __init__(self, mode: str = "dev", jwks_url: str | None = None, issuer: str | None = None,
                 audience: str | None = None, role_claim: str = "caduceus_role"):
        if mode not in ("dev", "oidc"):
            raise ValueError("auth mode must be dev or oidc")
        if mode == "oidc" and not (jwks_url and issuer and audience):
            raise ValueError("oidc mode needs jwks_url, issuer and audience")
        self.mode = mode
        self._jwks = jwt.PyJWKClient(jwks_url) if mode == "oidc" and jwks_url else None
        self._issuer, self._audience, self._role_claim = issuer, audience, role_claim

    def authenticate(self, dev_user: str | None, authorization: str | None) -> Principal:
        if self.mode == "dev":
            principal = DEV_USERS.get(dev_user or "")
            if principal is None:
                raise AuthError("unknown development user")
            return principal
        if not authorization or not authorization.lower().startswith("bearer "):
            raise AuthError("missing bearer token")
        token = authorization.split(" ", 1)[1]
        try:
            key = self._jwks.get_signing_key_from_jwt(token).key  # type: ignore[union-attr]
            claims = jwt.decode(token, key, algorithms=["RS256", "ES256"], audience=self._audience,
                                issuer=self._issuer)
        except jwt.PyJWTError as e:
            raise AuthError(f"invalid token: {type(e).__name__}") from e
        try:
            role = Role(claims[self._role_claim])
        except (KeyError, ValueError) as e:
            raise AuthError("token has no valid role claim") from e
        if role == Role.AGENT_RUNTIME:
            raise AuthError("service identities cannot sign in to the user interface")
        return Principal(claims["sub"], claims.get("name", claims["sub"]), role, claims.get("clinician_id"))
