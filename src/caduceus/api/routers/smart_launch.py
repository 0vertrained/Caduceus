"""Opening Caduceus from an EHR by SMART launch (docs/smart-launch.md): the launch, the record it reads, and a case
started from that record."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import JSONResponse

from caduceus.api.bodies import CallbackBody, EhrCaseBody, LaunchBody
from caduceus.api.dependencies import LiveDep, Specialist, TeamMember, visitor_of
from caduceus.api.sandboxes import SANDBOX_HEADER, Live
from caduceus.auth import Principal, Role
from caduceus.smart import LaunchRefused, SmartLaunch
from caduceus.workflow import ActionRefused

DEMO_EHR_RECORDS = 3         # patients opened from an EHR that one visitor's copy holds at once

router = APIRouter()


def get_smart(request: Request) -> SmartLaunch:
    return request.app.state.smart


SmartDep = Annotated[SmartLaunch, Depends(get_smart)]


def ehr_step(request: Request, live: Live, smart: SmartLaunch) -> str | None:
    """Checks before a step of a launch talks to the EHR: the launch is set up here, the request names the
    visitor's own copy of the demo (a shared record never lands in the shared set), and the launch limits."""
    # Not set up, or nowhere to keep what a launch reads (the Postgres store keeps no EHR records yet): refused
    # before anything is fetched, rather than after reading a whole record only to drop it.
    if not smart.enabled or live.svc.workflow.launched is None:
        raise HTTPException(404, "Opening Caduceus from an EHR is not set up here.")
    sandbox = request.headers.get(SANDBOX_HEADER) if live.demo else None
    if live.demo and not sandbox:
        raise HTTPException(400, "Open this page from the Caduceus demo site.")
    limits = request.app.state.demo_limits
    wait = limits.ehr_launch(visitor_of(request)) if limits else 0
    if wait:
        raise HTTPException(429, "Too many launches from an EHR from your address. Wait a few minutes and try "
                                 "again.", headers={"Retry-After": str(wait)})
    return sandbox


def launch_refused(live: Live, who: Principal, e: LaunchRefused) -> JSONResponse:
    live.svc.audit.append(who.id, who.role, "ehr.launch_refused", None, e.audit)
    return JSONResponse({"message": e.message}, status_code=e.status)


def load_record(live: Live, key: str) -> dict:
    launched = live.svc.workflow.launched
    data = launched.get(key) if launched else None
    if data is None:
        raise HTTPException(404, "That record from the EHR is not here: it goes when the demo starts over. Open "
                                 "Caduceus from the EHR again.")
    return data


@router.get("/smart")
def smart_info(smart: SmartDep) -> dict:
    """Whether Caduceus can be opened from an EHR here, and the client id a launcher should hold it to."""
    return {"enabled": smart.enabled, "client_id": smart.client_id if smart.enabled else None}


@router.post("/smart/launch")
def smart_launch(body: LaunchBody, who: Specialist, live: LiveDep, smart: SmartDep, request: Request):
    """The EHR opened Caduceus: check where from, discover its endpoints, and say where the browser goes next."""
    sandbox = ehr_step(request, live, smart)
    try:
        pending, url = smart.begin(body.iss, body.launch, body.redirect_uri, sandbox, who.id, live.now())
    except LaunchRefused as e:
        return launch_refused(live, who, e)
    live.svc.audit.append(who.id, who.role, "ehr.launch_started", None,
                          {"iss": pending.server.iss, "scope": pending.scope})
    return {"authorize_url": url}


@router.post("/smart/callback")
def smart_callback(body: CallbackBody, who: Specialist, live: LiveDep, smart: SmartDep, request: Request):
    """The EHR sent the browser back: exchange the code here, read the record, file it in this copy."""
    sandbox = ehr_step(request, live, smart)
    try:
        read = smart.finish(body.code, body.state, sandbox, who.id, live.now(),
                            live.now().astimezone(live.svc.tz).date(), request.app.state.lookback)
    except LaunchRefused as e:
        return launch_refused(live, who, e)
    try:
        key = live.svc.workflow.file_ehr_record(who, read, keep=DEMO_EHR_RECORDS if live.demo else None)
    except (LaunchRefused, ActionRefused) as e:
        # Read, then let go: the audit log says what was read and why it was not filed, never anything from it.
        why = e.audit if isinstance(e, LaunchRefused) else {"reason": "not_filed"}
        live.svc.audit.append(who.id, who.role, "ehr.launch_refused", None,
                              why | {"iss": read.iss, "discarded": read.counts})
        if isinstance(e, ActionRefused):
            raise
        return JSONResponse({"message": e.message}, status_code=e.status)
    live.presenter.forget(key)
    return {"record": key}


@router.get("/smart/records/{key}")
def ehr_record(key: str, who: TeamMember, live: LiveDep):
    data = load_record(live, key)
    live.svc.audit.append(who.id, who.role, "ehr.record_viewed", key)
    cases = live.svc.workflow.patient_cases(key)  # this patient's, from this read or an earlier one
    out = live.presenter.ehr_record(data, live.svc.workflow.ehr_preview(key), cases, live.now())
    return out | {"payers": sorted(live.svc.workflow.catalog["payers"]),
                  "read_only": who.role != Role.PA_SPECIALIST}


@router.post("/smart/records/{key}/case")
def ehr_case(key: str, body: EhrCaseBody, who: Specialist, live: LiveDep):
    load_record(live, key)
    return {"case_id": live.svc.workflow.open_from_ehr(who, key, body.service, body.side, body.payer).id}
