"""The work queue, and a case as a prior-authorization specialist works it: its pages and what they do with it."""

from __future__ import annotations

import re
from datetime import datetime

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import FileResponse

from caduceus.api.bodies import DraftBody, ReturnBody, UrgencyBody
from caduceus.api.dependencies import LiveDep, SignedIn, Specialist, TeamMember
from caduceus.api.views import case_view, dispatch_due, load_case, summary_of
from caduceus.auth import Role
from caduceus.fhir.store import fax_image

router = APIRouter()


@router.get("/queue")
def queue(who: TeamMember, live: LiveDep) -> dict:
    svc = live.svc
    dispatch_due(svc)
    local_today = live.now().astimezone(svc.tz).date()
    approved_today = sum(1 for c in svc.cases.all() if c.submission and c.submission.get("approved_by_id") == who.id
                         and datetime.fromisoformat(c.submission["approved_at"]).astimezone(svc.tz).date()
                         == local_today)
    return live.presenter.queue(svc.cases.all(), live.now(), approved_today)


@router.get("/cases/{case_id}")
def get_case(case_id: str, who: TeamMember, live: LiveDep, request: Request):
    dispatch_due(live.svc)
    return case_view(request, load_case(live, case_id), who)


@router.get("/cases/{case_id}/pages/{doc_id}")
def page(case_id: str, doc_id: str, who: TeamMember, live: LiveDep):
    try:
        out = live.presenter.page(doc_id, load_case(live, case_id))
    except KeyError as e:
        raise HTTPException(404, "That page is not part of this case.") from e
    live.svc.audit.append(who.id, who.role, "page.viewed", case_id, {"doc": doc_id})
    return out


@router.get("/cases/{case_id}/pages/{doc_id}/image")
def page_image(case_id: str, doc_id: str, who: TeamMember, live: LiveDep):
    """The image of a faxed page in the case's packet (the cover sheet), from the intake folder only."""
    case = load_case(live, case_id)
    doc = live.svc.store.document(doc_id) if doc_id in case.packet else None
    file = fax_image(doc) if doc else None
    if not file or not re.fullmatch(r"FAX-\d{4}\.png", file) or live.svc.workflow.fax_dir is None:
        raise HTTPException(404, "That page has no image.")
    live.svc.audit.append(who.id, who.role, "page.viewed", case_id, {"doc": doc_id, "image": True})
    return FileResponse(live.svc.workflow.fax_dir / file, media_type="image/png")


@router.post("/cases/{case_id}/request-attestation")
def request_attestation(case_id: str, who: SignedIn, live: LiveDep, request: Request):
    return case_view(request, live.svc.workflow.request_attestation(who, load_case(live, case_id).id), who)


@router.post("/cases/{case_id}/approve")
def approve(case_id: str, who: SignedIn, live: LiveDep, request: Request):
    return case_view(request, live.svc.workflow.approve(who, load_case(live, case_id).id), who)


@router.post("/cases/{case_id}/urgency")
def urgency(case_id: str, body: UrgencyBody, who: SignedIn, live: LiveDep, request: Request):
    """Ask the payer for an expedited decision (72 hours under the CMS rule), or go back to standard."""
    case = live.svc.workflow.set_urgency(who, load_case(live, case_id).id, body.expedited, body.reason)
    return case_view(request, case, who)


@router.post("/cases/{case_id}/return")
def return_case(case_id: str, body: ReturnBody, who: SignedIn, live: LiveDep, request: Request):
    case = live.svc.workflow.return_for_correction(who, load_case(live, case_id).id, body.reason)
    return case_view(request, case, who)


@router.put("/cases/{case_id}/draft")
def edit_draft(case_id: str, body: DraftBody, who: SignedIn, live: LiveDep, request: Request):
    return case_view(request, live.svc.workflow.edit_draft(who, load_case(live, case_id).id, body.text), who)


@router.post("/cases/{case_id}/remind")
def remind(case_id: str, who: Specialist, live: LiveDep, request: Request):
    return case_view(request, live.svc.workflow.remind(who, load_case(live, case_id).id), who)


@router.post("/cases/{case_id}/resubmitted")
def resubmitted(case_id: str, who: Specialist, live: LiveDep, request: Request):
    return case_view(request, live.svc.workflow.resubmit(who, load_case(live, case_id).id), who)


@router.post("/cases/{case_id}/recall")
def recall(case_id: str, who: Specialist, live: LiveDep, request: Request):
    return case_view(request, live.svc.workflow.recall(who, load_case(live, case_id).id), who)


@router.post("/cases/{case_id}/undo")
def undo(case_id: str, who: SignedIn, live: LiveDep, request: Request):
    case = live.svc.workflow.undo(who, load_case(live, case_id).id)
    if who.role == Role.CLINICIAN:
        return live.presenter.attestation(case, summary_of(case), live.now())
    return case_view(request, case, who)
