"""Answering the payer: the clinician's note when a payer pends a request with a question, and the appeal of a
denial. Each goes through a person's approval and the outbox, as the request did."""

from __future__ import annotations

from fastapi import APIRouter, Request

from caduceus.api.bodies import AppealBody
from caduceus.api.dependencies import LiveDep, SignedIn
from caduceus.api.views import case_view, load_case

router = APIRouter()


@router.post("/cases/{case_id}/request-note")
def request_note(case_id: str, who: SignedIn, live: LiveDep, request: Request):
    return case_view(request, live.svc.workflow.request_note(who, load_case(live, case_id).id), who)


@router.post("/cases/{case_id}/send-note")
def send_note(case_id: str, who: SignedIn, live: LiveDep, request: Request):
    return case_view(request, live.svc.workflow.approve_reply(who, load_case(live, case_id).id), who)


@router.put("/cases/{case_id}/appeal")
def edit_appeal(case_id: str, body: AppealBody, who: SignedIn, live: LiveDep, request: Request):
    return case_view(request, live.svc.workflow.edit_appeal(who, load_case(live, case_id).id, body.text), who)


@router.post("/cases/{case_id}/send-appeal")
def send_appeal(case_id: str, who: SignedIn, live: LiveDep, request: Request):
    return case_view(request, live.svc.workflow.approve_appeal(who, load_case(live, case_id).id), who)
