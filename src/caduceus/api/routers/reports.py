"""What the team reads back: a case's history, whether the audit log is intact, the outcomes counted from it, and
the coverage policies in force."""

from __future__ import annotations

from fastapi import APIRouter

from caduceus.api.dependencies import AuditorOrAdmin, LiveDep, TeamMember
from caduceus.api.views import dispatch_due, fax_of, load_case
from caduceus.audit.log import TamperDetected
from caduceus.auth import Role
from caduceus.cases import EHR_RECORD
from caduceus.outcomes import outcomes
from caduceus.policies import policies
from caduceus.timeline import timeline

router = APIRouter()


@router.get("/cases/{case_id}/history")
def history(case_id: str, who: TeamMember, live: LiveDep):
    load_case(live, case_id)
    return [{"seq": e.seq, "at": e.ts, "actor": e.actor, "role": e.role, "action": e.action, "detail": e.detail}
            for e in live.svc.audit.events(case_id)]


@router.get("/cases/{case_id}/timeline")
def case_timeline(case_id: str, who: TeamMember, live: LiveDep):
    case = load_case(live, case_id)
    events = live.svc.audit.events(case_id)
    fax = fax_of(live, case_id)
    if fax:  # a case that came by fax begins with the fax: received, read, checked, perhaps sent back first
        events = sorted(live.svc.audit.events(fax.id) + events, key=lambda e: e.seq)
    if EHR_RECORD.fullmatch(case.patient_id):  # one opened from the EHR begins with the EHR sharing the record
        events = sorted(live.svc.audit.events(case.patient_id) + events, key=lambda e: e.seq)
    return {"events": len(events), "can_verify": who.role in (Role.AUDITOR, Role.ADMIN),
            "entries": timeline(events, case.ordering_clinician["name"], live.svc.tz, live.now())}


@router.get("/audit/verify")
def verify_audit(who: AuditorOrAdmin, live: LiveDep) -> dict:
    try:
        return {"intact": True, "events": live.svc.audit.verify()}
    except TamperDetected as e:
        return {"intact": False, "message": str(e)}


@router.get("/outcomes")
def queue_outcomes(who: TeamMember, live: LiveDep):
    svc = live.svc
    dispatch_due(svc)
    sections = outcomes(svc.cases.all(), svc.audit.events(), svc.workflow.faxes.all() if svc.workflow.faxes else [])
    return {"demo": live.demo, "sections": [
        {"title": s["title"], "rows": [{"label": a, "value": b, "note": c} for a, b, c in s["rows"]]}
        for s in sections]}


@router.get("/policies")
def coverage_policies(who: TeamMember, live: LiveDep) -> dict:
    wf = live.svc.workflow
    return {"services": policies(wf.library, wf.engine.approved)}
