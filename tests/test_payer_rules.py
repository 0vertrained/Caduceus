"""Payer rules: coverage requirements at intake, the CMS decision clock, specific denials, and appeals."""

from __future__ import annotations

import base64

import pytest

from caduceus.auth import AGENT
from caduceus.workflow import ActionRefused
from tests.test_workflow_api import DOLORES, as_user, env  # noqa: F401 - env is a fixture

SLEEP = "2026-1038"   # UnitedHealthcare Medicare Advantage, CPAP, AHI 9 with hypertension
KNEE = "2026-1041"    # Original Medicare, total knee replacement
SAM = as_user("sam")


def send(svc, client, clock, case_id: str, path: str = "approve") -> dict:
    client.post(f"/api/cases/{case_id}/{path}", headers=DOLORES)
    clock.advance(seconds=31)
    assert case_id in svc.workflow.dispatch_due()
    return client.get(f"/api/cases/{case_id}", headers=DOLORES).json()


def test_intake_asks_the_payer_what_it_requires_and_checks_the_policy_version(env):  # noqa: F811
    svc, client, _ = env
    case = client.get(f"/api/cases/{SLEEP}", headers=DOLORES).json()
    assert case["requirements"]["matches"] is True and case["requirements"]["plan_type"] == "Medicare Advantage"
    calls = [e.detail["tool"] for e in svc.audit.events(SLEEP) if e.action == "tool.call"]
    assert "payer.coverage_requirements" in calls


def test_a_payer_policy_that_differs_from_the_approved_one_blocks_approval(env):  # noqa: F811
    svc, client, _ = env
    svc.workflow.payer._published["dev-lumbar-mri@2026-09"] = "0" * 64  # the payer published a new version
    svc.workflow.prepare("2026-1036")
    case = client.get("/api/cases/2026-1036", headers=DOLORES).json()
    assert case["requirements"]["matches"] is False and case["actions"]["can_approve"] is False
    assert "publishes a different version" in case["actions"]["approve_blocked_reason"]
    assert client.post("/api/cases/2026-1036/approve", headers=DOLORES).status_code == 409


def test_the_request_answers_the_payers_documentation_questions(env):  # noqa: F811
    svc, _, _ = env
    from caduceus.payer import build_request_bundle
    bundle = build_request_bundle(svc.cases.get(SLEEP))
    qr = next(e["resource"] for e in bundle["entry"] if e["resource"]["resourceType"] == "QuestionnaireResponse")
    assert [i["linkId"] for i in qr["item"]] == ["osa_diagnosis", "sleep_test", "severity_qualifies", "face_to_face"]
    assert qr["item"][2]["answer"][0]["valueCoding"]["code"] == "met"
    claim = bundle["entry"][0]["resource"]
    assert claim["supportingInfo"][-1]["valueReference"]["reference"] == f"QuestionnaireResponse/{qr['id']}"


def test_the_decision_clock_follows_the_cms_rule_and_original_medicare_is_outside_it(env):  # noqa: F811
    svc, client, clock = env
    knee = send(svc, client, clock, KNEE)
    outside = knee["payer_clock"]
    assert outside["applies"] is False and "Original Medicare is not covered" in outside["words"]

    client.post("/api/cases/2026-1043/request-attestation", headers=DOLORES)
    client.post("/api/attestations/2026-1043", headers=as_user("dr.reyes"),
                json={"answers": {"red_flags_excluded": "yes"}})
    pended = send(svc, client, clock, "2026-1043")
    assert pended["state"] == "pended" and pended["payer_clock"]["decided"] is False
    assert pended["payer_clock"]["words"].startswith("Due by November 5, 2026")


def test_a_specific_denial_gets_an_appeal_that_a_person_approves(env):  # noqa: F811
    svc, client, clock = env
    sent: list[dict] = []
    appeal = svc.workflow.payer.appeal
    svc.workflow.payer.appeal = lambda bundle, *a: sent.append(bundle) or appeal(bundle, *a)

    case = send(svc, client, clock, SLEEP)
    assert case["state"] == "denied" and case["status"]["word"] == "Denied; appeal drafted"
    assert case["payer_clock"]["words"] == "Decided in under a minute, within the 7-day limit."
    a = case["appeal"]
    assert "below 15" in a["reason"] and a["criterion"] == "Sleep apnea severity qualifies"
    assert "between 5 and 14" in a["policy_sentence"] and "hypertension" in a["record"].lower()
    assert "between 5 and 14" in a["draft"]["text"] and "reversed" in a["draft"]["text"]
    assert case["actions"]["can_send_appeal"] and case["actions"]["can_return"]
    rows = [r for g in client.get("/api/queue", headers=DOLORES).json()["groups"] for r in g["cases"]]
    assert any(r["id"] == SLEEP and r["status"]["key"] == "denied" for r in rows)

    edited = a["draft"]["text"] + " Dr. Priya Patel is available for a peer-to-peer discussion."
    assert client.put(f"/api/cases/{SLEEP}/appeal", headers=SAM, json={"text": edited}).status_code == 409
    assert client.put(f"/api/cases/{SLEEP}/appeal", headers=DOLORES, json={"text": " "}).status_code == 409
    client.put(f"/api/cases/{SLEEP}/appeal", headers=DOLORES, json={"text": edited})
    case = client.post(f"/api/cases/{SLEEP}/send-appeal", headers=DOLORES).json()
    assert case["status"]["key"] == "sending" and case["undo"]
    assert client.post(f"/api/cases/{SLEEP}/undo", headers=DOLORES).json()["state"] == "denied"

    case = send(svc, client, clock, SLEEP, "send-appeal")
    assert case["state"] == "approved" and case["payer_response"]["decision"]["display"] == "Certified on appeal"
    letter = sent[0]["entry"][0]["resource"]["supportingInfo"][-1]["valueAttachment"]["data"]
    assert base64.b64decode(letter).decode() == edited
    assert {"appeal.drafted", "appeal.edited", "appeal.approved"} <= {e.action for e in svc.audit.events(SLEEP)}


def test_an_appeal_changed_after_approval_is_not_sent(env):  # noqa: F811
    svc, client, clock = env
    send(svc, client, clock, SLEEP)
    client.post(f"/api/cases/{SLEEP}/send-appeal", headers=DOLORES)
    case = svc.cases.get(SLEEP)
    case.appeal["text"] += " Also approve a second device."
    svc.cases.save(case)
    with pytest.raises(ActionRefused, match="changed after it was approved"):
        svc.workflow.gateway.call(AGENT, SLEEP, "submission", "payer.appeal", case_id=SLEEP,
                                  approval_seq=case.submission["approval_seq"])
