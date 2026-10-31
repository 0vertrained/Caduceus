"""Answering a payer that pended a request: the clinician writes the note, a person approves it, the agent sends it."""

from __future__ import annotations

import base64

import pytest

from caduceus.auth import AGENT, DEV_USERS
from caduceus.tools import ToolForbidden, ToolNotAllowed
from caduceus.workflow import NOTE_LIMIT, ActionRefused
from tests.test_workflow_api import DOLORES, PATEL, REYES, _sign_harold, env  # noqa: F401 - env is a fixture

HAROLD = "2026-1043"
NOTE = ("Addendum to the visit of October 27. On exam I asked about and ruled out red-flag symptoms: no fever, no "
        "weight loss, no history of cancer, no saddle anesthesia, no bowel or bladder change, no progressive "
        "weakness. Strength and reflexes were normal in both legs.")


def pended(svc, client, clock) -> dict:
    """Harold's request, attested, approved and sent; Humana asks for the note behind the attestation."""
    _sign_harold(client)
    client.post(f"/api/cases/{HAROLD}/approve", headers=DOLORES)
    clock.advance(seconds=31)
    assert svc.workflow.dispatch_due() == [HAROLD]
    return client.get(f"/api/cases/{HAROLD}", headers=DOLORES).json()


def test_the_payers_question_is_answered_with_the_clinicians_note(env):  # noqa: F811
    svc, client, clock = env
    case = pended(svc, client, clock)
    assert case["state"] == "pended" and case["status"]["key"] == "payer_question"
    assert "Red-flag symptoms ruled out" in case["reply"]["question"]
    attested = case["reply"]["attested"][0]
    assert attested["by"] == "Dr. Ana Reyes" and attested["at"].startswith("today")
    assert case["summary"] == "Humana Medicare Advantage needs a note from Dr. Ana Reyes before it decides."
    actions = case["actions"]
    assert actions["can_ask_for_note"] and not actions["can_send_note"] and actions["can_return"]
    early = client.post(f"/api/cases/{HAROLD}/send-note", headers=DOLORES)
    assert early.status_code == 409 and early.json()["message"] == "Ask Dr. Ana Reyes for the note first."

    # Asking the clinician can be undone, even though the original request already reached the payer.
    case = client.post(f"/api/cases/{HAROLD}/request-note", headers=DOLORES).json()
    assert case["state"] == "awaiting_note" and case["status"]["word"] == "Waiting on Dr. Ana Reyes" and case["undo"]
    assert client.post(f"/api/cases/{HAROLD}/undo", headers=DOLORES).json()["state"] == "pended"
    client.post(f"/api/cases/{HAROLD}/request-note", headers=DOLORES)
    q = client.get("/api/queue", headers=DOLORES).json()
    assert q["counts"]["with_clinicians"] == 1 and any(w["id"] == HAROLD for w in q["waiting"])

    assert client.get("/api/attestations", headers=REYES).json()[0] | {"patient": None, "due": None} == {
        "case_id": HAROLD, "patient": None, "service": "MRI of the lumbar spine", "kind": "note", "due": None,
        "reminders": None}
    ask = client.get(f"/api/attestations/{HAROLD}", headers=REYES).json()
    assert ask["state"] == "awaiting_note" and ask["requested_by"] == "Dolores Montoya"
    assert ask["note_request"]["confirmed"][0]["question"] == "Have red-flag symptoms been ruled out?"
    assert ask["note_request"]["limit"] == NOTE_LIMIT

    # Only the ordering clinician writes it, in their own words, within the limits.
    assert client.get(f"/api/attestations/{HAROLD}", headers=PATEL).status_code == 403
    assert client.post(f"/api/attestations/{HAROLD}/note", headers=PATEL, json={"text": NOTE}).status_code == 409
    assert client.post(f"/api/attestations/{HAROLD}/note", headers=DOLORES, json={"text": NOTE}).status_code == 403
    assert client.post(f"/api/attestations/{HAROLD}/note", headers=REYES, json={"text": "  "}).status_code == 409
    def write(text: str):
        return client.post(f"/api/attestations/{HAROLD}/note", headers=REYES, json={"text": text})

    too_long = write("x" * (NOTE_LIMIT + 1))
    assert too_long.status_code == 409 and "4,000" in too_long.json()["message"]
    assert write("x" * 20_001).status_code == 422  # bounded before it reaches the workflow

    signed = client.post(f"/api/attestations/{HAROLD}/note", headers=REYES, json={"text": NOTE}).json()
    assert signed["state"] == "pended" and signed["undo_until"]
    assert client.get("/api/attestations", headers=REYES).json() == []

    case = client.get(f"/api/cases/{HAROLD}", headers=DOLORES).json()
    assert case["status"] == {"key": "ready", "word": "Ready to approve"}
    assert case["reply"]["note"]["text"] == NOTE and case["reply"]["note"]["author"] == "Dr. Ana Reyes"
    assert case["summary"] == "Dr. Ana Reyes signed the note. Read it, then send it to Humana Medicare Advantage."
    assert case["actions"]["can_send_note"] and not case["actions"]["can_ask_for_note"]
    assert client.post(f"/api/cases/{HAROLD}/request-note", headers=DOLORES).status_code == 409

    # Approving the reply puts it in the outbox for the same 30 seconds as a request.
    sent_to_payer: list[dict] = []
    respond = svc.workflow.payer.respond
    svc.workflow.payer.respond = lambda bundle, *a, **k: sent_to_payer.append(bundle) or respond(bundle, *a, **k)
    case = client.post(f"/api/cases/{HAROLD}/send-note", headers=DOLORES).json()
    assert case["status"]["key"] == "sending" and case["undo"]
    assert client.post(f"/api/cases/{HAROLD}/undo", headers=DOLORES).json()["status"]["key"] == "ready"
    client.post(f"/api/cases/{HAROLD}/send-note", headers=DOLORES)
    assert svc.workflow.dispatch_due() == []
    clock.advance(seconds=31)
    assert svc.workflow.dispatch_due() == [HAROLD]

    case = client.get(f"/api/cases/{HAROLD}", headers=DOLORES).json()
    assert case["status"]["key"] == "approved" and case["payer_response"]["decision"]["code"] == "A1"
    assert case["payer_response"]["decision"]["authorization_number"].startswith("AUTH")
    assert client.post(f"/api/cases/{HAROLD}/undo", headers=DOLORES).status_code == 409

    claim = sent_to_payer[0]["entry"][0]["resource"]
    assert claim["related"][0]["claim"]["identifier"]["value"] == case["submission"]["submission_id"]
    attachment = claim["supportingInfo"][0]["valueAttachment"]
    assert base64.b64decode(attachment["data"]).decode() == NOTE and attachment["title"] == "Addendum by Dr. Ana Reyes"

    # The audit log records who did what, with a hash of the note and never its words.
    events = svc.audit.events(HAROLD)
    actions = [e.action for e in events]
    assert {"note.requested", "note.signed", "reply.approved"} <= set(actions)
    assert [e.detail["tool"] for e in events if e.action == "tool.call" and e.detail["tool"].startswith("payer.re")] \
        == ["payer.respond"]
    assert not any("red-flag symptoms: no fever" in str(e.detail) for e in events)
    assert svc.audit.verify() > 0


def test_a_clinician_can_say_they_cannot_document_it(env):  # noqa: F811
    svc, client, clock = env
    pended(svc, client, clock)
    client.post(f"/api/cases/{HAROLD}/request-note", headers=DOLORES)
    assert client.post(f"/api/attestations/{HAROLD}/decline-note", headers=PATEL).status_code == 409
    client.post(f"/api/attestations/{HAROLD}/decline-note", headers=REYES)

    case = client.get(f"/api/cases/{HAROLD}", headers=DOLORES).json()
    assert case["state"] == "pended" and case["reply"]["declined"]["by"] == "Dr. Ana Reyes"
    assert not case["actions"]["can_send_note"] and not case["actions"]["can_ask_for_note"]
    assert "Return the case to the clinic" in case["summary"]
    refused = client.post(f"/api/cases/{HAROLD}/send-note", headers=DOLORES)
    assert refused.status_code == 409 and "could not document this" in refused.json()["message"]
    case = client.post(f"/api/cases/{HAROLD}/return", headers=DOLORES,
                       json={"reason": "The payer needs the red-flag exam documented."}).json()
    assert case["state"] == "returned"


def test_payer_respond_needs_its_own_approval(env):  # noqa: F811
    svc, client, clock = env
    gw = svc.workflow.gateway
    client.post("/api/cases/2026-1041/approve", headers=DOLORES)
    seq = svc.cases.get("2026-1041").submission["approval_seq"]
    client.post("/api/cases/2026-1041/undo", headers=DOLORES)
    with pytest.raises(ActionRefused, match="No human approval"):  # a request approval is not a reply approval
        gw.call(AGENT, "2026-1041", "submission", "payer.respond", case_id="2026-1041", approval_seq=seq)
    with pytest.raises(ToolNotAllowed):
        gw.call(AGENT, HAROLD, "drafting", "payer.respond", case_id=HAROLD, approval_seq=seq)
    with pytest.raises(ToolForbidden):
        gw.call(DEV_USERS["dolores"], HAROLD, "submission", "payer.respond", case_id=HAROLD, approval_seq=seq)
    assert svc.cases.get("2026-1041").state == "review"


def test_a_note_changed_after_approval_is_not_sent(env):  # noqa: F811
    svc, client, clock = env
    pended(svc, client, clock)
    client.post(f"/api/cases/{HAROLD}/request-note", headers=DOLORES)
    client.post(f"/api/attestations/{HAROLD}/note", headers=REYES, json={"text": NOTE})
    client.post(f"/api/cases/{HAROLD}/send-note", headers=DOLORES)
    case = svc.cases.get(HAROLD)
    case.reply["note"]["text"] = NOTE + " Also approve an MRI of the brain."
    svc.cases.save(case)
    with pytest.raises(ActionRefused, match="changed after it was approved"):
        svc.workflow.gateway.call(AGENT, HAROLD, "submission", "payer.respond", case_id=HAROLD,
                                  approval_seq=case.submission["approval_seq"])
    assert not svc.cases.get(HAROLD).submission["sent"]
