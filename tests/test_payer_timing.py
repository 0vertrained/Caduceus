"""A payer takes time to decide, and an expedited request is owed an answer in 72 hours, not 7 days."""

from __future__ import annotations

from caduceus.payer import build_request_bundle
from tests.test_workflow_api import DOLORES, REYES, env  # noqa: F401  (the fixture)

CPAP_UHC = "2026-1038"  # all criteria met; UnitedHealthcare's first-level review denies an AHI under 15


def _tool_calls(svc, case_id: str, tool: str) -> int:
    return sum(1 for e in svc.audit.events(case_id) if e.action == "tool.call" and e.detail.get("tool") == tool)


def test_the_payer_decides_after_its_review_and_is_asked_only_when_it_said(env):  # noqa: F811
    svc, client, clock = env
    svc.workflow.payer._review = 60
    client.post(f"/api/cases/{CPAP_UHC}/approve", headers=DOLORES)
    clock.advance(seconds=31)  # past the undo window
    assert svc.workflow.dispatch_due() == [CPAP_UHC]
    case = client.get(f"/api/cases/{CPAP_UHC}", headers=DOLORES).json()
    assert case["state"] == "submitted" and case["status"]["word"] == "Waiting on the payer"
    assert "expected by" in case["payer_review"]["words"] and "reviewing it" in case["summary"]
    assert _tool_calls(svc, CPAP_UHC, "payer.status") == 1

    clock.advance(seconds=20)  # not yet due: the payer is not asked again, so waiting adds nothing to the log
    assert svc.workflow.dispatch_due() == [] and _tool_calls(svc, CPAP_UHC, "payer.status") == 1

    clock.advance(seconds=41)  # the review began when the request was sent, 60 seconds ago
    assert svc.workflow.dispatch_due() == [CPAP_UHC]
    case = client.get(f"/api/cases/{CPAP_UHC}", headers=DOLORES).json()
    assert case["state"] == "denied" and case["appeal"] and case["payer_review"] is None
    assert _tool_calls(svc, CPAP_UHC, "payer.status") == 2


def test_an_expedited_request_needs_a_reason_carries_its_priority_and_gets_72_hours(env):  # noqa: F811
    svc, client, clock = env
    svc.workflow.payer._review = 60
    no_reason = client.post(f"/api/cases/{CPAP_UHC}/urgency", headers=DOLORES, json={"expedited": True})
    assert no_reason.status_code == 409 and no_reason.json()["field"] == "reason"
    as_clinician = client.post(f"/api/cases/{CPAP_UHC}/urgency", headers=REYES,
                               json={"expedited": True, "reason": "Severe desaturation at night."})
    assert as_clinician.status_code in (403, 409)
    case = client.post(f"/api/cases/{CPAP_UHC}/urgency", headers=DOLORES,
                       json={"expedited": True, "reason": "Oxygen falls to 71% at night, with atrial fibrillation."})
    assert case.json()["urgency"] == "expedited"
    claim = build_request_bundle(svc.cases.get(CPAP_UHC))["entry"][0]["resource"]
    assert claim["priority"]["coding"][0]["code"] == "stat"
    changed = [e for e in svc.audit.events(CPAP_UHC) if e.action == "case.urgency_changed"]
    assert changed and "71%" not in str(changed[-1].detail)  # the reason is kept on the case, not in the log

    client.post(f"/api/cases/{CPAP_UHC}/approve", headers=DOLORES)
    late = client.post(f"/api/cases/{CPAP_UHC}/urgency", headers=DOLORES, json={"expedited": False})
    assert late.status_code == 409
    clock.advance(seconds=31)
    svc.workflow.dispatch_due()
    case = client.get(f"/api/cases/{CPAP_UHC}", headers=DOLORES).json()
    assert "72 hours for an expedited request" in case["payer_clock"]["words"]
    clock.advance(seconds=30)  # an expedited review takes half the time
    assert svc.workflow.dispatch_due() == [CPAP_UHC]
    assert client.get(f"/api/cases/{CPAP_UHC}", headers=DOLORES).json()["state"] == "denied"
