"""The five tool servers over the Model Context Protocol, each tool a thin wrapper over the ToolGateway, so any MCP
client meets the same role and step checks and audit records as the agent.

The caller is the identity the server was launched as over stdio (CADUCEUS_MCP_ROLE, default agent_runtime), and a
bearer JWT verified against the identity provider over streamable HTTP.

    python -m caduceus.mcp_servers ehr                 # stdio
    python -m caduceus.mcp_servers ehr --http 8101     # streamable HTTP, bearer tokens required
"""

from __future__ import annotations

import argparse
import inspect
import os
from collections.abc import Callable
from typing import Any

import jwt
from mcp.server.auth.middleware.auth_context import get_access_token
from mcp.server.auth.provider import AccessToken
from mcp.server.mcpserver import MCPServer

from caduceus.auth import Principal, Role
from caduceus.tools import ToolGateway, ToolSpec

SERVERS = ("ehr", "documents", "policies", "tickets", "payer")


class JwtTokenVerifier:
    """Verifies bearer tokens for the HTTP transport against the identity provider's signing keys."""

    def __init__(self, jwks_url: str, issuer: str, audience: str, role_claim: str = "caduceus_role"):
        self._jwks = jwt.PyJWKClient(jwks_url)
        self._issuer, self._audience, self._role_claim = issuer, audience, role_claim

    async def verify_token(self, token: str) -> AccessToken | None:
        try:
            key = self._jwks.get_signing_key_from_jwt(token).key
            claims = jwt.decode(token, key, algorithms=["RS256", "ES256"], audience=self._audience,
                                issuer=self._issuer)
            Role(claims[self._role_claim])
        except (jwt.PyJWTError, KeyError, ValueError):
            return None
        return AccessToken(token=token, client_id=claims.get("azp", claims["sub"]), scopes=[],
                           expires_at=claims.get("exp"), subject=claims["sub"], claims=claims, resource=self._audience)


def principal_from_context(default: Principal) -> Principal:
    token = get_access_token()
    if token is None or not token.claims:
        return default
    c = token.claims
    return Principal(c["sub"], c.get("name", c["sub"]), Role(c["caduceus_role"]), c.get("clinician_id"))


def _wrap(gateway: ToolGateway, spec: ToolSpec, who: Callable[[], Principal]) -> Callable[..., Any]:
    original = inspect.signature(spec.fn)
    forwards_case = "case_id" in original.parameters

    def tool(case_id: str, step: str, **kwargs: Any) -> Any:
        if forwards_case:
            kwargs["case_id"] = case_id
        return gateway.call(who(), case_id, step, spec.name, **kwargs)

    params = [inspect.Parameter("case_id", inspect.Parameter.KEYWORD_ONLY, annotation=str),
              inspect.Parameter("step", inspect.Parameter.KEYWORD_ONLY, annotation=str)]
    params += [p.replace(kind=inspect.Parameter.KEYWORD_ONLY) for p in original.parameters.values()
               if p.name != "case_id"]
    tool.__signature__ = inspect.Signature(params, return_annotation=original.return_annotation)  # type: ignore[attr-defined]
    tool.__annotations__ = {p.name: p.annotation for p in params} | {"return": original.return_annotation}
    tool.__name__ = spec.name.split(".", 1)[1]
    return tool


def build_server(name: str, gateway: ToolGateway, launched_as: Principal,
                 token_verifier: JwtTokenVerifier | None = None) -> MCPServer:
    if name not in SERVERS:
        raise ValueError(f"unknown server {name}; choose from {', '.join(SERVERS)}")
    server = MCPServer(f"caduceus-{name}", instructions=f"Mesa Verde {name} tools. Every call needs the case id "
                       "and the workflow step; calls outside the step's allow-list are refused.",
                       token_verifier=token_verifier)
    for spec in gateway.for_server(name):
        server.add_tool(_wrap(gateway, spec, lambda: principal_from_context(launched_as)),
                        name=spec.name.split(".", 1)[1], description=spec.description)
    return server


def main() -> None:
    from caduceus.app_factory import build_services

    ap = argparse.ArgumentParser()
    ap.add_argument("server", choices=SERVERS)
    ap.add_argument("--http", type=int, help="serve streamable HTTP on this port (bearer tokens required)")
    args = ap.parse_args()
    services = build_services()
    role = Role(os.environ.get("CADUCEUS_MCP_ROLE", Role.AGENT_RUNTIME.value))
    launched_as = Principal(f"mcp-{args.server}", f"MCP {args.server}", role)
    verifier = None
    if args.http:
        s = services.settings
        if not (s.oidc_jwks_url and s.oidc_issuer and s.oidc_audience):
            raise SystemExit("HTTP transport needs CADUCEUS_OIDC_JWKS_URL, _ISSUER and _AUDIENCE")
        verifier = JwtTokenVerifier(s.oidc_jwks_url, s.oidc_issuer, s.oidc_audience)
    server = build_server(args.server, services.workflow.gateway, launched_as, verifier)
    if args.http:
        server.run("streamable-http", port=args.http)
    else:
        server.run("stdio")


if __name__ == "__main__":
    main()
