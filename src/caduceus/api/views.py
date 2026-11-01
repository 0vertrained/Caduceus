"""How the routes read a case: what is due is sent first, a missing case is a 404, and each view is recorded."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from fastapi import HTTPException, Request

from caduceus.api.sandboxes import Live
from caduceus.app_factory import Services
from caduceus.auth import Principal, Role
from caduceus.cases import EHR_RECORD, Case, Fax
from caduceus.drafting import Drafter

# A page that refreshes itself while a payer or the model works is one view, not one per refresh; someone who comes
# back later is recorded again.
VIEW_AGAIN = timedelta(minutes=1)


def dispatch_due(svc: Services) -> None:
    """Reading a case must never fail because sending another one did; failures are audited instead."""
    try:
        svc.workflow.dispatch_due()
    except Exception as e:  # noqa: BLE001
        try:
            svc.audit.append("caduceus-agent", "agent_runtime", "dispatch.error", None,
                             {"error": type(e).__name__})
        except Exception:  # noqa: BLE001, S110 - the audit store itself is unavailable; nothing safer to do
            pass


def load_case(live: Live, case_id: str) -> Case:
    case = live.svc.cases.get(case_id)
    if case is None:
        raise HTTPException(404, "There is no case with that number.")
    return case


def fax_of(live: Live, case_id: str) -> Fax | None:
    faxes = live.svc.workflow.faxes
    return next((f for f in faxes.all() if f.case_id == case_id), None) if faxes else None


def summary_of(case: Case) -> dict:
    """The summary written when the clinician was asked (the model's, once it is ready), or from the facts."""
    return case.chart_summary or Drafter.template_summary(case, case.attestation_requested or [])


def record_view(request: Request, who: Principal, action: str, case_id: str) -> None:
    """Audit that a person opened a case, unless they did within the last VIEW_AGAIN."""
    live: Live = request.app.state.live
    last = next((e for e in reversed(live.svc.audit.events(case_id)) if e.actor == who.id and e.action == action),
                None)
    if last is None or datetime.now(UTC) - datetime.fromisoformat(last.ts) >= request.app.state.view_again:
        live.svc.audit.append(who.id, who.role, action, case_id)


def case_view(request: Request, case: Case, who: Principal) -> dict:
    """The case as this person sees it, recording that they opened it."""
    live: Live = request.app.state.live
    record_view(request, who, "case.viewed", case.id)
    out = live.presenter.case(load_case(live, case.id), live.now(), who.id, read_only=who.role != Role.PA_SPECIALIST)
    fax = fax_of(live, case.id)
    out["came_by_fax"] = fax and live.presenter.came_by_fax(
        fax, live.svc.audit.events(fax.id), live.svc.workflow.intake_form(fax)["clinic"], live.now())
    out["came_from_ehr"] = live.presenter.came_from_ehr(
        case, live.svc.audit.events(case.id), _ehr_record_of(live, case), live.now()) \
        if EHR_RECORD.fullmatch(case.patient_id) else None
    return out


def _ehr_record_of(live: Live, case: Case) -> dict | None:
    launched = live.svc.workflow.launched
    return launched.get(case.patient_id) if launched else None
