"""The ordering clinician's side: the requests waiting on them, signing an answer for each criterion asked about,
and writing the note a payer asked for, or saying they cannot."""

from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, HTTPException, Request

from caduceus.api.bodies import AttestBody, NoteBody
from caduceus.api.dependencies import Clinician, LiveDep
from caduceus.api.sandboxes import Live
from caduceus.api.views import load_case, record_view, summary_of
from caduceus.cases import Case, State
from caduceus.presenter import clock

NO_SUMMARY = {"text": "", "route": None, "model": None}  # after signing, the summary is not shown again

router = APIRouter()


def signed(live: Live, case: Case) -> dict:
    """The clinician's screen once they have answered, with how long they have to undo it."""
    out = live.presenter.attestation(case, NO_SUMMARY, live.now())
    out["undo_until"] = case.last_action.undo_until.isoformat() if case.last_action else None
    return out


@router.get("/attestations")
def my_attestations(who: Clinician, live: LiveDep):
    mine = [c for c in live.svc.cases.all() if c.state in (State.AWAITING_ATTESTATION, State.AWAITING_NOTE)
            and c.ordering_clinician["id"] == who.clinician_id]
    out = []
    for c in sorted(mine, key=lambda c: c.due_at):  # most urgent first: the request due soonest
        row, sent = live.presenter.row(c, live.now()), c.reminders_since_asked()
        out.append({"case_id": c.id, "patient": row["patient"], "service": c.service["short"],
                    "kind": "note" if c.state == State.AWAITING_NOTE else "attestation", "due": row["due"],
                    "reminders": {"count": len(sent), "by": sent[-1]["by"],
                                  "last": clock(datetime.fromisoformat(sent[-1]["at"]), live.svc.tz)}
                    if sent else None})
    return out


@router.get("/attestations/{case_id}")
def attestation(case_id: str, who: Clinician, live: LiveDep, request: Request):
    case = load_case(live, case_id)
    if case.ordering_clinician["id"] != who.clinician_id:
        raise HTTPException(403, "Only the ordering clinician can see this request.")
    record_view(request, who, "attestation.viewed", case_id)
    return live.presenter.attestation(case, summary_of(case), live.now())


@router.post("/attestations/{case_id}")
def sign(case_id: str, body: AttestBody, who: Clinician, live: LiveDep):
    case = live.svc.workflow.sign_attestation(who, load_case(live, case_id).id, body.answers)
    return signed(live, case) | {"answers": body.answers}


@router.post("/attestations/{case_id}/note")
def sign_note(case_id: str, body: NoteBody, who: Clinician, live: LiveDep):
    return signed(live, live.svc.workflow.sign_note(who, load_case(live, case_id).id, body.text))


@router.post("/attestations/{case_id}/decline-note")
def decline_note(case_id: str, who: Clinician, live: LiveDep):
    return signed(live, live.svc.workflow.decline_note(who, load_case(live, case_id).id))
