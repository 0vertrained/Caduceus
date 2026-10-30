"""After a person acts: recalling an approval that has not been sent, and a returned case the clinic resubmits."""

from __future__ import annotations

import pytest

from caduceus.auth import AGENT, Principal, Role
from caduceus.workflow import ActionRefused
from tests.test_payer_reply import NOTE, pended
from tests.test_workflow_api import DOLORES, REYES, _sign_harold, as_user, env  # noqa: F401 - env is a fixture

MARIA = Principal("maria", "Maria Ortega", Role.PA_SPECIALIST)  # a second PA specialist


def test_an_approved_request_can_be_recalled_until_it_is_sent(env):  # noqa: F811
    svc, client, clock = env
    client.post("/api/cases/2026-1041/approve", headers=DOLORES)
    seq = svc.cases.get("2026-1041").submission["approval_seq"]
    # Inside the 30 seconds, the approver can undo it; another specialist's case page offers a recall instead.
    assert client.app.state.live.presenter.case(svc.cases.get("2026-1041"), clock(), "maria")["actions"]["can_recall"]
    clock.advance(seconds=31)  # the window has passed, and the outbox has not run yet

    late = client.post("/api/cases/2026-1041/undo", headers=DOLORES)
    assert late.status_code == 409
    assert late.json()["message"] == ("The 30 seconds to undo have passed, but it has not been sent yet. Recall it "
                                      "instead.")
    mine = client.app.state.live.presenter.case(svc.cases.get("2026-1041"), clock(), "dolores")
    assert mine["undo"] is None and mine["actions"]["can_recall"] is True
    assert client.post("/api/cases/2026-1041/recall", headers=REYES).status_code == 403
    assert client.post("/api/cases/2026-1041/recall", headers=as_user("sam")).status_code == 403

    case = svc.workflow.recall(MARIA, "2026-1041")
    assert case.state == "review" and case.submission is None and case.last_action is None
    assert svc.workflow.dispatch_due() == []  # nothing left in the outbox
    with pytest.raises(ActionRefused, match="Nothing approved is waiting"):
        svc.workflow.gateway.call(AGENT, "2026-1041", "submission", "payer.submit", case_id="2026-1041",
                                  approval_seq=seq)
    recalled = next(e for e in svc.audit.events("2026-1041") if e.action == "case.recalled")
    assert recalled.actor == "maria" and recalled.detail == {"kind": "request", "approval_seq": seq,
                                                             "approved_by": "dolores"}
    said = [e["what"] for e in client.get("/api/cases/2026-1041/timeline", headers=DOLORES).json()["entries"]]
    assert "Recalled the request before it was sent. It is back in review." in said
    assert client.post("/api/cases/2026-1041/recall", headers=DOLORES).status_code == 409  # nothing to recall now
    assert client.post("/api/cases/2026-1041/approve", headers=DOLORES).json()["state"] == "submitted"


def test_once_sent_it_cannot_be_recalled_and_the_refusal_says_so(env):  # noqa: F811
    svc, client, clock = env
    client.post("/api/cases/2026-1041/approve", headers=DOLORES)
    clock.advance(seconds=31)
    assert svc.workflow.dispatch_due() == ["2026-1041"]
    for path in ("recall", "undo"):
        refused = client.post(f"/api/cases/2026-1041/{path}", headers=DOLORES)
        assert refused.status_code == 409
        assert refused.json()["message"] == ("The request reached Medicare today at 1:00 pm, so it cannot be undone "
                                             "or recalled.")
    # A late undo of something that sends nothing does not offer a recall.
    client.post("/api/cases/2026-1033/request-attestation", headers=DOLORES)
    clock.advance(seconds=45)
    late = client.post("/api/cases/2026-1033/undo", headers=DOLORES).json()["message"]
    assert late == "The 30 seconds to undo have passed."


def test_a_recalled_note_goes_back_to_the_payers_question(env):  # noqa: F811
    svc, client, clock = env
    pended(svc, client, clock)
    client.post("/api/cases/2026-1043/request-note", headers=DOLORES)
    client.post("/api/attestations/2026-1043/note", headers=REYES, json={"text": NOTE})
    client.post("/api/cases/2026-1043/send-note", headers=DOLORES)
    clock.advance(seconds=31)
    case = client.post("/api/cases/2026-1043/recall", headers=DOLORES).json()
    assert case["state"] == "pended" and case["reply"]["note"]["text"] == NOTE
    assert case["actions"]["can_send_note"] and case["submission"]["sent"]  # the original request, as it was
    assert svc.workflow.dispatch_due() == []
    said = [e["what"] for e in client.get("/api/cases/2026-1043/timeline", headers=DOLORES).json()["entries"]]
    assert "Recalled the note before it was sent. It waits to be sent again." in said


def test_a_recalled_appeal_goes_back_to_the_denial(env):  # noqa: F811
    svc, client, clock = env
    client.post("/api/cases/2026-1038/approve", headers=DOLORES)
    clock.advance(seconds=31)
    svc.workflow.dispatch_due()
    assert svc.cases.get("2026-1038").state == "denied"
    client.post("/api/cases/2026-1038/send-appeal", headers=DOLORES)
    clock.advance(seconds=31)
    case = client.post("/api/cases/2026-1038/recall", headers=DOLORES).json()
    assert case["state"] == "denied" and case["actions"]["can_send_appeal"] and case["appeal"]["draft"]["text"]
    assert svc.workflow.dispatch_due() == []


def test_a_returned_case_the_clinic_resubmits_is_prepared_again(env):  # noqa: F811
    svc, client, clock = env
    assert client.post("/api/cases/2026-1043/resubmitted", headers=DOLORES).status_code == 409  # not returned
    _sign_harold(client)  # signed for the referral as it was
    client.post("/api/cases/2026-1043/return", headers=DOLORES, json={"reason": "The red-flag exam is not documented."})
    q = client.get("/api/queue", headers=DOLORES).json()
    waiting = next(w for w in q["waiting"] if w["id"] == "2026-1043")
    assert waiting["status"]["key"] == "returned" and waiting["next"] == "Open it when the clinic resubmits."
    returned = client.get("/api/cases/2026-1043", headers=DOLORES).json()
    assert returned["actions"]["can_resubmit"] is True
    assert client.get("/api/cases/2026-1043", headers=as_user("sam")).json()["actions"]["can_resubmit"] is False

    assert client.post("/api/cases/2026-1043/resubmitted", headers=REYES).status_code == 403
    assert client.post("/api/cases/2026-1043/resubmitted", headers=as_user("sam")).status_code == 403
    clock.advance(minutes=5)
    case = client.post("/api/cases/2026-1043/resubmitted", headers=DOLORES).json()
    # Prepared afresh: checked again, a new draft, and what the record does not answer goes to the clinician again.
    assert case["state"] == "review" and case["returned_reason"] is None and case["undo"] is None
    assert case["status"]["key"] == "needs_clinician" and case["actions"]["can_request_attestation"]
    assert case["criteria"]["checked_at"] == "1:05 pm" and case["draft"]["text"]
    actions = [e.action for e in svc.audit.events("2026-1043")]
    assert actions.index("case.resubmitted") < max(i for i, a in enumerate(actions) if a == "criteria.evaluated")
    q = client.get("/api/queue", headers=DOLORES).json()
    assert all(w["id"] != "2026-1043" for w in q["waiting"]) and q["groups"][0]["cases"][0]["id"] == "2026-1043"
    said = [e["what"] for e in client.get("/api/cases/2026-1043/timeline", headers=DOLORES).json()["entries"]]
    assert "Marked that the clinic resubmitted it. Caduceus prepares it again from the start." in said
    assert client.post("/api/cases/2026-1043/resubmitted", headers=DOLORES).status_code == 409
