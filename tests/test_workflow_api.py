"""The workflow through the HTTP API, the tool gateway and the MCP servers."""

from __future__ import annotations

import asyncio
import json
import sqlite3
from datetime import UTC, datetime, timedelta

import httpx
import pytest
from fastapi.testclient import TestClient

from caduceus.api import create_app
from caduceus.app_factory import Settings, build_services
from caduceus.auth import AGENT, DEV_USERS, Principal, Role
from caduceus.deid.safe_harbor import SafeHarborDeidentifier
from caduceus.drafting import Drafter, patient_context
from caduceus.mcp_servers import build_server
from caduceus.router import Router
from caduceus.router.providers import AnthropicMessagesProvider
from caduceus.seed import seed
from caduceus.tools import ToolForbidden, ToolNotAllowed
from caduceus.workflow import ActionRefused
from tests.conftest import DATA, TEST_KEY

T0 = datetime(2026, 10, 29, 19, 0, tzinfo=UTC)  # 1:00 pm in Santa Fe


class Clock:
    def __init__(self, t: datetime):
        self.t = t

    def __call__(self) -> datetime:
        return self.t

    def advance(self, **kw) -> None:
        self.t += timedelta(**kw)


@pytest.fixture()
def env():
    clock = Clock(T0)
    svc = build_services(Settings(data_dir=DATA, db_path=":memory:", auth_mode="dev", deid_key=TEST_KEY.decode()),
                         conn=sqlite3.connect(":memory:", check_same_thread=False))
    svc.workflow.now = clock
    seed(svc, now=T0)
    client = TestClient(create_app(svc, dispatch_interval=None))
    return svc, client, clock


def as_user(user: str) -> dict:
    return {"X-Caduceus-User": user}


DOLORES, REYES, PATEL = as_user("dolores"), as_user("dr.reyes"), as_user("dr.patel")


def test_seed_prepares_every_case(env):
    svc, _, _ = env
    cases = svc.cases.all()
    assert len(cases) == 11 and all(c.state == "review" and c.determination and c.draft for c in cases)
    assert {c.draft["route"] for c in cases} == {"template"}
    assert svc.audit.verify() > 100


def test_queue_groups_by_due_time_in_words(env):
    _, client, _ = env
    q = client.get("/api/queue", headers=DOLORES).json()
    groups = {g["key"]: [c["id"] for c in g["cases"]] for g in q["groups"]}
    assert groups["today"] == ["2026-1043", "2026-1041", "2026-1038"]
    assert groups["tomorrow"] == ["2026-1035", "2026-1036"]
    assert len(groups["later"]) == 6
    first = q["groups"][0]["cases"][0]
    assert first["due"] == {"words": "In 1 hour 30 minutes", "sub": "due at 2:30 pm", "urgent": True, "group": "today"}
    assert first["status"] == {"key": "needs_clinician", "word": "Needs a clinician"}
    second = q["groups"][0]["cases"][1]
    assert second["due"]["words"] == "In 4 hours" and second["due"]["urgent"] is False


def test_harold_path_attest_approve_undo_dispatch(env):
    svc, client, clock = env
    case = client.get("/api/cases/2026-1043", headers=DOLORES).json()
    assert case["summary"] == ("Three of four criteria are met. Dr. Ana Reyes needs to confirm the last one "
                               "before you can approve.")
    assert case["actions"]["can_approve"] is False and case["actions"]["can_request_attestation"] is True
    blocked = client.post("/api/cases/2026-1043/approve", headers=DOLORES)
    assert blocked.status_code == 409 and "Dr. Ana Reyes needs to confirm" in blocked.json()["message"]

    case = client.post("/api/cases/2026-1043/request-attestation", headers=DOLORES).json()
    assert case["status"]["word"] == "Waiting on Dr. Ana Reyes" and case["undo"]["seconds"] == 30

    assert client.get("/api/attestations/2026-1043", headers=PATEL).status_code == 403
    ask = client.get("/api/attestations/2026-1043", headers=REYES).json()
    assert ask["requested_by"] == "Dolores Montoya"
    assert ask["questions"][0]["question"] == "Have red-flag symptoms been ruled out?"
    assert "does not say" in ask["chart_summary"]

    wrong = client.post("/api/attestations/2026-1043", headers=REYES, json={"answers": {"lbp_duration": "yes"}})
    assert wrong.status_code == 409
    client.post("/api/attestations/2026-1043", headers=REYES, json={"answers": {"red_flags_excluded": "yes"}})
    case = client.get("/api/cases/2026-1043", headers=DOLORES).json()
    assert case["actions"]["can_approve"] and case["status"]["key"] == "ready"
    attested = next(r for r in case["criteria"]["results"] if r["id"] == "red_flags_excluded")
    assert attested["status"] == "met" and attested["attested_by"] == "Dr. Ana Reyes"

    case = client.post("/api/cases/2026-1043/approve", headers=DOLORES).json()
    assert case["status"]["key"] == "sending" and case["undo"]
    case = client.post("/api/cases/2026-1043/undo", headers=DOLORES).json()
    assert case["status"]["key"] == "ready"

    client.post("/api/cases/2026-1043/approve", headers=DOLORES)
    assert svc.workflow.dispatch_due() == []  # still inside the undo window
    clock.advance(seconds=31)
    assert svc.workflow.dispatch_due() == ["2026-1043"]
    case = client.get("/api/cases/2026-1043", headers=DOLORES).json()
    assert case["status"]["key"] == "payer_question"  # this payer asks for the note behind an attestation
    assert "red-flag" in case["reply"]["question"].lower()  # answering it: tests/test_payer_reply.py
    late = client.post("/api/cases/2026-1043/undo", headers=DOLORES)
    assert late.status_code == 409


def _sign_harold(client):
    client.post("/api/cases/2026-1043/request-attestation", headers=DOLORES)
    return client.post("/api/attestations/2026-1043", headers=REYES, json={"answers": {"red_flags_excluded": "yes"}})


def test_signing_rewrites_the_draft_so_the_letter_matches(env):
    _, client, _ = env
    before = client.get("/api/cases/2026-1043", headers=DOLORES).json()["draft"]["text"]
    assert "Awaiting clinician attestation" in before
    assert _sign_harold(client).status_code == 200
    after = client.get("/api/cases/2026-1043", headers=DOLORES).json()
    assert "Awaiting clinician attestation" not in after["draft"]["text"]
    assert "Attested by Dr. Ana Reyes" in after["draft"]["text"]
    assert after["actions"]["can_approve"] is True


def test_signing_never_overwrites_a_persons_edit(env):
    _, client, _ = env
    mine = "My own wording for this request. Red flags pending the clinician."
    assert client.put("/api/cases/2026-1043/draft", headers=DOLORES, json={"text": mine}).status_code == 200
    _sign_harold(client)
    c = client.get("/api/cases/2026-1043", headers=DOLORES).json()
    assert c["draft"]["text"] == mine
    assert c["actions"]["can_approve"] is False
    assert "Read the draft again" in c["actions"]["approve_blocked_reason"]
    assert client.post("/api/cases/2026-1043/approve", headers=DOLORES).status_code == 409
    client.put("/api/cases/2026-1043/draft", headers=DOLORES, json={"text": mine.replace(" pending", " ruled out by")})
    assert client.post("/api/cases/2026-1043/approve", headers=DOLORES).status_code == 200


def test_concurrent_dispatchers_send_each_request_once(env):
    import threading
    svc, client, clock = env
    client.post("/api/cases/2026-1041/approve", headers=DOLORES)
    clock.advance(seconds=31)
    results: list[list[str]] = []
    threads = [threading.Thread(target=lambda: results.append(svc.workflow.dispatch_due())) for _ in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert sorted(sum(results, [])) == ["2026-1041"]
    events = svc.audit.events("2026-1041")
    submits = [e for e in events if e.action == "tool.call" and e.detail["tool"] == "payer.submit"]
    assert len(submits) == 1


def test_complete_case_is_approved_by_the_payer(env):
    svc, client, clock = env
    client.post("/api/cases/2026-1041/approve", headers=DOLORES)
    clock.advance(seconds=31)
    svc.workflow.dispatch_due()
    case = client.get("/api/cases/2026-1041", headers=DOLORES).json()
    assert case["status"]["key"] == "approved" and case["payer_response"]["decision"]["code"] == "A1"
    q = client.get("/api/queue", headers=DOLORES).json()
    assert q["counts"]["approved_today"] == 1


def test_edit_draft_is_recorded(env):
    svc, client, _ = env
    case = client.put("/api/cases/2026-1041/draft", headers=DOLORES, json={"text": "Edited justification."}).json()
    assert case["draft"]["text"] == "Edited justification." and case["draft"]["edited_by"] == "Dolores Montoya"
    assert client.put("/api/cases/2026-1041/draft", headers=DOLORES, json={"text": "  "}).status_code == 409
    assert client.put("/api/cases/2026-1041/draft", headers=DOLORES, json={"text": "x" * 20_001}).status_code == 422
    assert "draft.edited" in [e.action for e in svc.audit.events("2026-1041")]


def test_return_needs_a_reason_and_shows_in_waiting(env):
    _, client, _ = env
    assert client.post("/api/cases/2026-1029/return", headers=DOLORES, json={"reason": " "}).status_code == 409
    assert client.post("/api/cases/2026-1029/return", headers=DOLORES, json={"reason": "x" * 2_001}).status_code == 422
    client.post("/api/cases/2026-1029/return", headers=DOLORES, json={"reason": "Pain began 3 weeks ago; resubmit"})
    q = client.get("/api/queue", headers=DOLORES).json()
    assert any(w["id"] == "2026-1029" and w["status"]["key"] == "returned" for w in q["waiting"])


def test_undo_is_personal_and_time_limited(env):
    svc, client, clock = env
    client.post("/api/cases/2026-1033/request-attestation", headers=DOLORES)
    other = Principal("maria", "Maria Ortega", Role.PA_SPECIALIST)
    with pytest.raises(ActionRefused, match="nothing of yours"):
        svc.workflow.undo(other, "2026-1033")
    clock.advance(seconds=45)
    assert client.post("/api/cases/2026-1033/undo", headers=DOLORES).status_code == 409


def test_roles_and_identity(env):
    _, client, _ = env
    assert client.get("/api/queue", headers=as_user("nobody")).status_code == 401
    assert client.get("/api/queue").status_code == 401
    assert client.get("/api/queue", headers=REYES).status_code == 403
    assert client.post("/api/cases/2026-1041/approve", headers=as_user("sam")).status_code == 409
    assert client.get("/api/cases/2026-1041/history", headers=as_user("sam")).status_code == 200


def test_security_headers_and_no_store(env):
    _, client, _ = env
    r = client.get("/api/cases/2026-1043", headers=DOLORES)
    assert r.headers["Cache-Control"] == "no-store"
    assert "frame-ancestors 'none'" in r.headers["Content-Security-Policy"]
    assert r.headers["X-Content-Type-Options"] == "nosniff"


def test_evidence_page_is_part_of_the_case(env):
    svc, client, _ = env
    case = client.get("/api/cases/2026-1043", headers=DOLORES).json()
    ev = case["criteria"]["results"][0]["evidence"][0]
    page = client.get(f"/api/cases/2026-1043/pages/{ev['document_id']}", headers=DOLORES).json()
    assert ev["sentence"] in page["text"] and page["page"] == ev["page"]
    other = svc.cases.get("2026-1041").packet[0]
    assert client.get(f"/api/cases/2026-1043/pages/{other}", headers=DOLORES).status_code == 404


def test_gateway_refuses_wrong_role_and_wrong_step(env):
    svc, _, _ = env
    gw = svc.workflow.gateway
    with pytest.raises(ToolNotAllowed):
        gw.call(AGENT, "2026-1041", "enrichment", "payer.submit", case_id="2026-1041", approval_seq=1)
    with pytest.raises(ToolForbidden):
        gw.call(DEV_USERS["dolores"], "2026-1041", "review", "tickets.set_state", case_id="2026-1041",
                state="approved", reason="x")
    actions = [e.action for e in svc.audit.events("2026-1041")]
    assert "tool.not_allowed" in actions and "tool.forbidden" in actions


def test_payer_submit_requires_a_recorded_human_approval(env):
    svc, _, _ = env
    with pytest.raises(ActionRefused, match="No human approval"):
        svc.workflow.gateway.call(AGENT, "2026-1041", "submission", "payer.submit", case_id="2026-1041",
                                  approval_seq=999999)


def test_an_undone_approval_cannot_be_replayed(env):
    """The approval stays in the audit log after an undo, but payer.submit sends only what is in the outbox."""
    svc, client, _ = env
    client.post("/api/cases/2026-1041/approve", headers=DOLORES)
    seq = svc.cases.get("2026-1041").submission["approval_seq"]
    client.post("/api/cases/2026-1041/undo", headers=DOLORES)
    with pytest.raises(ActionRefused, match="Nothing approved is waiting"):
        svc.workflow.gateway.call(AGENT, "2026-1041", "submission", "payer.submit", case_id="2026-1041",
                                  approval_seq=seq)
    assert svc.cases.get("2026-1041").state == "review"


def test_mcp_servers_enforce_the_same_rules(env):
    svc, _, _ = env
    ehr = build_server("ehr", svc.workflow.gateway, AGENT)
    case = svc.cases.get("2026-1043")
    args = {"case_id": case.id, "patient_id": case.patient_id, "as_of": case.as_of}

    async def run():
        names = {t.name for t in await ehr.list_tools()}
        assert {"list_active_conditions", "list_procedures", "get_patient_summary"} <= names
        ok = await ehr.call_tool("list_active_conditions", args | {"step": "enrichment"})
        conditions = [json.loads(item.text) for item in ok.content]  # one content item per list element
        assert any("back pain" in c["display"].lower() for c in conditions)
        with pytest.raises(Exception) as refused:
            await ehr.call_tool("list_active_conditions", args | {"step": "submission"})
        assert isinstance(refused.value.__cause__, ToolNotAllowed)

    asyncio.run(run())
    clinician_payer = build_server("tickets", svc.workflow.gateway, DEV_USERS["dr.reyes"])

    async def run2():
        with pytest.raises(Exception) as refused:
            await clinician_payer.call_tool("set_state", {"case_id": "2026-1043", "step": "review",
                                                          "state": "approved", "reason": "x"})
        assert isinstance(refused.value.__cause__, ToolForbidden)

    asyncio.run(run2())


def test_end_to_end_drafting_never_sends_patient_identifiers(env):
    """Real Synthea identifiers for the patient must not appear in any byte sent to the external model."""
    svc, _, _ = env
    wire: list[bytes] = []

    def handler(request: httpx.Request) -> httpx.Response:
        wire.append(request.content)
        body = json.loads(request.content)
        tokens = [t for t in ("[NAME_1]", "[DATE_1]") if t in body["messages"][0]["content"]]
        text = f"{tokens[0] if tokens else 'The patient'} meets the documented criteria for imaging."
        return httpx.Response(200, json={"content": [{"type": "text", "text": text}],
                                         "usage": {"input_tokens": 100, "output_tokens": 12}})

    deid = SafeHarborDeidentifier(TEST_KEY)
    client = httpx.Client(transport=httpx.MockTransport(handler))
    external = AnthropicMessagesProvider("sk-test", "frontier", client=client)
    svc.workflow.drafter = Drafter(Router(None, external, deid), deid)
    case = svc.workflow.prepare("2026-1043")

    patient = svc.store.record(case.patient_id).patient
    ctx = patient_context(patient)
    sent = b"".join(wire).decode()
    assert wire and case.draft["route"] == "external"
    leaked = [v for _, v in ctx.known_values() if v.lower() in sent.lower()]
    assert leaked == []
    assert ctx.names[0].split()[0] in case.draft["text"]  # re-identified locally


def test_settings_are_secure_by_default(monkeypatch):
    from caduceus.app_factory import InsecureConfiguration
    for name in ("CADUCEUS_AUTH_MODE", "CADUCEUS_DEID_KEY"):
        monkeypatch.delenv(name, raising=False)
    s = Settings(data_dir=DATA, db_path=":memory:")
    assert s.auth_mode == "oidc"  # development sign-in must be asked for by name
    with pytest.raises(InsecureConfiguration, match="CADUCEUS_DEID_KEY"):
        s.check()
    monkeypatch.setenv("CADUCEUS_AUTH_MODE", "dev")
    assert Settings().auth_mode == "dev"  # the environment is read when settings are built, not at import
    with pytest.raises(InsecureConfiguration, match="nowhere to run"):
        Settings(auth_mode="oidc", deid_key=TEST_KEY.decode(), external_api_key="x").check()


def test_health_says_nothing_and_audit_verification_needs_a_role(env):
    _, client, _ = env
    assert client.get("/api/health").json() == {"ok": True}
    assert client.get("/api/audit/verify", headers=DOLORES).status_code == 403
    r = client.get("/api/audit/verify", headers=as_user("sam")).json()
    assert r["intact"] is True and r["events"] > 0


def test_starts_on_a_system_without_a_time_zone_database(tmp_path):
    """Minimal images (Wolfi, distroless) ship no /usr/share/zoneinfo; clinic time must still work."""
    import os
    import subprocess
    import sys
    code = ("from caduceus.app_factory import Settings, build_services; "
            "s = build_services(Settings(db_path=':memory:', auth_mode='dev')); print(s.tz)")
    env = os.environ | {"PYTHONTZPATH": str(tmp_path / "no-zoneinfo")}
    out = subprocess.run([sys.executable, "-c", code], env=env,  # noqa: S603 - this interpreter, fixed code
                         capture_output=True, text=True, cwd=DATA.parent)
    assert out.returncode == 0, out.stderr
    assert out.stdout.strip() == "America/Denver"


SAM = as_user("sam")


def test_the_case_history_reads_in_plain_words_and_only_an_auditor_verifies_it(env):
    svc, client, _ = env
    _sign_harold(client)
    client.get("/api/cases/2026-1043", headers=DOLORES)
    client.get("/api/cases/2026-1043", headers=DOLORES)
    with pytest.raises(ToolNotAllowed):
        svc.workflow.gateway.call(AGENT, "2026-1043", "drafting", "payer.submit", case_id="2026-1043", approval_seq=1)

    mine = client.get("/api/cases/2026-1043/timeline", headers=DOLORES).json()
    whats = [e["what"] for e in mine["entries"]]
    assert mine["can_verify"] is False and mine["events"] == len(svc.audit.events("2026-1043"))
    assert "Received the referral, 4 pages." in whats
    assert "Used 6 tools to read the record, the referral packet and the payer's requirements." in whats
    assert "Asked Dr. Ana Reyes to confirm 1 item." in whats and "Signed, answering yes." in whats
    assert any(w == "Opened the case (2 times)." for w in whats)  # repeated views collapse into one line
    refused = [e for e in mine["entries"] if e["kind"] == "refused"]
    assert refused and "not allowed during drafting" in refused[0]["what"] and refused[0]["who"] == "Caduceus"
    signed = next(e for e in mine["entries"] if e["what"].startswith("Signed"))
    assert signed["who"] == "Dr. Ana Reyes" and signed["role"] == "ordering clinician" and len(signed["hash"]) == 12

    assert client.get("/api/cases/2026-1043/timeline", headers=SAM).json()["can_verify"] is True
    assert client.get("/api/cases/2026-1043/timeline", headers=REYES).status_code == 403


def test_an_auditor_reads_the_queue_and_cases_but_cannot_act(env):
    _, client, _ = env
    assert client.get("/api/queue", headers=SAM).status_code == 200
    case = client.get("/api/cases/2026-1041", headers=SAM).json()
    assert case["read_only"] is True and not any(v for k, v in case["actions"].items() if k.startswith("can_"))
    assert case["summary"].startswith("Read only.")
    assert client.post("/api/cases/2026-1041/approve", headers=SAM).status_code == 409
    assert client.get("/api/cases/2026-1041", headers=DOLORES).json()["read_only"] is False
