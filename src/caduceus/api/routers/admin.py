"""The administrator's view of the deployment, and the switch that stops every call to the external model."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException

from caduceus.api.bodies import SwitchBody
from caduceus.api.dependencies import Admin, LiveDep
from caduceus.audit.log import TamperDetected
from caduceus.auth import DEV_USERS
from caduceus.policies import policies
from caduceus.presenter import moment

router = APIRouter()


@router.get("/admin")
def administration(who: Admin, live: LiveDep) -> dict:
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
                  "last": moment(events[-1].ts, live.now(), svc.tz) if events else None},
        "policies": [{"title": p["running"]["title"], "version": p["running"]["version"],
                      "intact": p["running"]["intact"]}
                     for p in policies(svc.workflow.library, svc.workflow.engine.approved)],
    }


@router.post("/admin/external-route")
def switch_external_route(body: SwitchBody, who: Admin, live: LiveDep) -> dict:
    """Stop, or restart, every call to the external model. Kept in the audit log, which the next start reads."""
    svc = live.svc
    if svc.router is None or not svc.router.external_enabled:
        raise HTTPException(409, "No external model is configured, so there is nothing to switch.")
    svc.router.switch_external(body.on)
    svc.audit.append(who.id, who.role, "admin.external_route", None, {"on": body.on})
    return administration(who, live)
