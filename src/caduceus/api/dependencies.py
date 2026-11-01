"""What the routes depend on: the services serving the request, the person signed in, and the visitor's address."""

from __future__ import annotations

from collections.abc import Callable
from typing import Annotated

from fastapi import Depends, Header, HTTPException, Request

from caduceus.api.sandboxes import Live
from caduceus.auth import AuthError, Principal, Role


def get_live(request: Request) -> Live:
    return request.app.state.live


LiveDep = Annotated[Live, Depends(get_live)]


def principal(live: LiveDep, x_caduceus_user: Annotated[str | None, Header()] = None,
              authorization: Annotated[str | None, Header()] = None) -> Principal:
    try:
        return live.svc.authenticator.authenticate(x_caduceus_user, authorization)
    except AuthError as e:
        raise HTTPException(401, "Sign in to continue.") from e


def role(*allowed: Role) -> Callable[[Principal], Principal]:
    def check(who: Annotated[Principal, Depends(principal)]) -> Principal:
        if who.role not in allowed:
            raise HTTPException(403, "Your role cannot see this.")
        return who
    return check


SignedIn = Annotated[Principal, Depends(principal)]
Specialist = Annotated[Principal, Depends(role(Role.PA_SPECIALIST))]
Clinician = Annotated[Principal, Depends(role(Role.CLINICIAN))]
Admin = Annotated[Principal, Depends(role(Role.ADMIN))]
AuditorOrAdmin = Annotated[Principal, Depends(role(Role.AUDITOR, Role.ADMIN))]
# Everyone who works the queue or oversees it; an ordering clinician sees only what is asked of them.
TeamMember = Annotated[Principal, Depends(role(Role.PA_SPECIALIST, Role.ADMIN, Role.AUDITOR))]


def visitor_of(request: Request) -> str:
    """Behind Netlify the visitor's address is in the forwarded headers, not the connection."""
    return (request.headers.get("x-nf-client-connection-ip")
            or request.headers.get("x-forwarded-for", "").split(",")[0].strip()
            or (request.client.host if request.client else "unknown"))
