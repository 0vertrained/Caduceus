"""Faxed referrals: read inside the boundary, checked by a person against the page, and confirmed as a case."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse

from caduceus.api.bodies import AskClinicBody, ConfirmFaxBody, MatchBody
from caduceus.api.dependencies import LiveDep, SignedIn, Specialist, TeamMember
from caduceus.api.sandboxes import Live
from caduceus.cases import Fax
from caduceus.intake import FaxReadingUnavailable
from caduceus.presenter import moment

router = APIRouter()


def fax_view(live: Live, fax: Fax, detail: bool = False) -> dict:
    fields = (fax.reading or {}).get("fields", [])
    by_key = {f["key"]: f for f in fields}
    checking = live.svc.workflow.intake_form(fax) if fax.reading else None
    out = {"id": fax.id, "status": fax.status, "received": moment(fax.received_at, live.now(), live.svc.tz),
           "pages": len(fax.pages) + 1, "referral": by_key.get("referral", {}).get("value"),
           "patient": by_key.get("patient_name", {}).get("value"),
           "to_check": sum(1 for f in checking["fields"] if f["problem"]) if checking else 0,
           "clinic": checking and checking["clinic"], "case_id": fax.case_id, "request": None}
    if fax.request:
        labels = {f["key"]: f["label"] for f in checking["fields"]} if checking else {}
        r = fax.request
        out["request"] = {"kind": r["kind"], "missing": [labels.get(k, k) for k in r["missing"]], "to": r["to"],
                          "text": r["text"], "asked_by": r["asked_by"],
                          "asked": moment(r["asked_at"], live.now(), live.svc.tz),
                          "answered": r.get("answered_at") and moment(r["answered_at"], live.now(), live.svc.tz)}
    if detail:
        out |= {"fields": fields, "size": (fax.reading or {}).get("size"),
                "engine": (fax.reading or {}).get("engine"), "image": f"/api/intake/{fax.id}/image"}
        if checking:
            out |= {"form": checking["fields"], "sender": checking["sender"], "choices": checking["choices"]}
    return out


def read_new_faxes(live: Live) -> bool:
    try:
        live.svc.workflow.read_faxes()
        return True
    except FaxReadingUnavailable:
        return False


def load_fax(live: Live, fax_id: str) -> Fax:
    fax = live.svc.workflow.faxes.get(fax_id) if live.svc.workflow.faxes else None
    if fax is None:
        raise HTTPException(404, "There is no fax with that number.")
    return fax


@router.get("/intake")
def intake(who: TeamMember, live: LiveDep):
    available = read_new_faxes(live)
    faxes = live.svc.workflow.faxes.all() if live.svc.workflow.faxes else []
    return {"available": available, "faxes": [fax_view(live, f) for f in faxes]}


@router.get("/intake/{fax_id}")
def intake_item(fax_id: str, who: TeamMember, live: LiveDep):
    read_new_faxes(live)
    fax = load_fax(live, fax_id)
    live.svc.audit.append(who.id, who.role, "fax.viewed", fax.id)
    return fax_view(live, fax, detail=True)


@router.get("/intake/{fax_id}/image")
def intake_image(fax_id: str, who: TeamMember, live: LiveDep):
    fax = load_fax(live, fax_id)
    return FileResponse(live.svc.workflow.fax_dir / fax.file, media_type="image/png")  # type: ignore[operator]


@router.post("/intake/{fax_id}/match")
def match_patient(fax_id: str, body: MatchBody, who: Specialist, live: LiveDep):
    load_fax(live, fax_id)
    return live.svc.workflow.match_patient(who, fax_id, body.name, body.birth_date, body.ref)


@router.post("/intake/{fax_id}/ask-clinic")
def ask_clinic(fax_id: str, body: AskClinicBody, who: Specialist, live: LiveDep):
    load_fax(live, fax_id)
    fax = live.svc.workflow.ask_clinic(who, fax_id, body.missing, body.resend, body.referral)
    return fax_view(live, fax, detail=True)


@router.post("/intake/{fax_id}/clinic-sent")
def clinic_sent(fax_id: str, who: Specialist, live: LiveDep):
    load_fax(live, fax_id)
    return fax_view(live, live.svc.workflow.clinic_sent(who, fax_id), detail=True)


@router.post("/intake/{fax_id}/confirm")
def confirm_fax(fax_id: str, body: ConfirmFaxBody, who: SignedIn, live: LiveDep):
    load_fax(live, fax_id)
    case = live.svc.workflow.confirm_fax(who, fax_id, body.values, body.checked)
    return {"case_id": case.id}
