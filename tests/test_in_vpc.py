"""The model inside the network: it alone may see the patient's details. It writes what the chart shows for the
clinician in the background, since it takes tens of seconds on a CPU, and the screens say which route wrote what."""

from __future__ import annotations

import json
import threading
from datetime import timedelta

import httpx
import pytest
from fastapi.testclient import TestClient

from caduceus.api import create_app
from caduceus.app_factory import Settings, build_services
from caduceus.drafting import Drafter
from caduceus.router import Router
from caduceus.router.providers import OpenAICompatibleProvider
from caduceus.seed import seed
from tests.conftest import DATA, TEST_KEY
from tests.test_workflow_api import DOLORES, REYES, T0

CASE = "2026-1043"  # red flags not in the record: Dr. Ana Reyes is asked
# Faithful sentences for the case's three documented criteria, as the model would write them.
SENTENCES = {
    "lbp_duration": "Low back pain radiating to the left buttock has been documented for 9 weeks.",
    "conservative_care": "She completed 6 weeks of physical therapy with minimal improvement in pain or function.",
    "recent_xray": "A lumbar spine X-ray on September 1, 2026 showed degenerative disc disease at L4 to L5.",
}
SUMMARY = " ".join(SENTENCES.values())
UNANSWERED = "The chart does not say whether this was done: red-flag symptoms ruled out by the clinician."
DRAFT = "A request written inside the network from de-identified facts."


class Model:
    """An in-network model server, answered here: what it was sent, and an outage on request. A request for a
    summary (one that asks for JSON) gets `sentences` for the criteria it names; any other gets a draft."""

    def __init__(self):
        self.sent: list[dict] = []
        self.down = False
        self.sentences: dict[str, str] | str = dict(SENTENCES)

    def __call__(self, request: httpx.Request) -> httpx.Response:
        if self.down:
            return httpx.Response(503)
        body = json.loads(request.content)
        self.sent.append(body)
        schema = body.get("response_format", {}).get("json_schema", {}).get("schema")
        if schema is None:
            content = DRAFT
        elif isinstance(self.sentences, str):
            content = self.sentences
        else:
            content = json.dumps({k: self.sentences.get(k, "") for k in schema["required"]})
        return httpx.Response(200, json={"choices": [{"message": {"content": content}}],
                                         "usage": {"prompt_tokens": 400, "completion_tokens": 30}})


@pytest.fixture()
def env():
    model = Model()
    svc = build_services(Settings(data_dir=DATA, db_path=":memory:", auth_mode="dev", deid_key=TEST_KEY.decode()))
    in_vpc = OpenAICompatibleProvider("http://model.internal/v1", "qwen2.5-1.5b-instruct",
                                      client=httpx.Client(transport=httpx.MockTransport(model)))
    router = Router(in_vpc, None, svc.workflow.drafter._deid)
    svc.router, svc.workflow.drafter = router, Drafter(router, svc.workflow.drafter._deid)
    svc.workflow.now = lambda: T0
    jobs: list = []
    svc.workflow.later = lambda fn, *args: jobs.append((fn, args))  # run when the test says
    seed(svc, now=T0)
    return svc, TestClient(create_app(svc, dispatch_interval=None)), model, jobs


def run(jobs: list) -> None:
    while jobs:
        fn, args = jobs.pop(0)
        fn(*args)


def test_the_clinician_sees_the_facts_at_once_then_the_in_network_models_summary(env):
    svc, client, model, jobs = env
    model.sent.clear()  # seeding drafted every case on this model, from de-identified facts
    client.post(f"/api/cases/{CASE}/request-attestation", headers=DOLORES)
    first = client.get(f"/api/attestations/{CASE}", headers=REYES).json()
    assert first["chart_summary_by"]["route"] == "template"  # nothing waits on the model
    assert "does not say" in first["chart_summary"] and first["chart_summary_pending"] is True

    run(jobs)
    (asked,) = model.sent
    assert asked["max_tokens"] == 400
    patient = svc.store.record(svc.cases.get(CASE).patient_id).patient
    name = " ".join(patient["name"][0]["given"][:1] + [patient["name"][0]["family"]])
    facts = asked["messages"][1]["content"]
    assert name in facts  # this route may see who the patient is; nothing else does
    # The model is given only what the chart documents, a block per criterion, and asked for a sentence each.
    assert asked["response_format"]["json_schema"]["schema"]["required"] == list(SENTENCES)
    assert all(f"[{c}]" in facts for c in SENTENCES) and "Red-flag" not in facts and "awaiting" not in facts
    after = client.get(f"/api/attestations/{CASE}", headers=REYES).json()
    assert after["chart_summary"] == f"{SUMMARY} {UNANSWERED}"
    assert after["chart_summary_by"] == {"route": "phi", "model": "qwen2.5-1.5b-instruct", "fallback_reason": None}
    assert after["chart_summary_pending"] is False
    (written,) = [e for e in svc.audit.events(CASE) if e.action == "summary.written"]
    assert written.detail["route"] == "phi" and SUMMARY not in json.dumps(written.detail)
    history = client.get(f"/api/cases/{CASE}/timeline", headers=DOLORES).json()["entries"]
    assert any("with the model inside the network" in e["what"] for e in history)


def test_a_sentence_that_says_more_than_the_record_is_replaced_with_the_records_words(env):
    svc, client, model, jobs = env
    model.sentences = SENTENCES | {"conservative_care": "She tried physical therapy for 6 weeks without improvement."}
    client.post(f"/api/cases/{CASE}/request-attestation", headers=DOLORES)
    run(jobs)
    summary = client.get(f"/api/attestations/{CASE}", headers=REYES).json()
    assert "without improvement" not in summary["chart_summary"]
    assert "Conservative treatment tried first: physical therapy for 6 weeks" in summary["chart_summary"]
    assert SENTENCES["lbp_duration"] in summary["chart_summary"] and summary["chart_summary_by"]["route"] == "phi"
    (written,) = [e for e in svc.audit.events(CASE) if e.action == "summary.written"]
    assert written.detail["replaced"] == [{"criterion_id": "conservative_care", "reason": "negation"}]
    history = client.get(f"/api/cases/{CASE}/timeline", headers=DOLORES).json()["entries"]
    assert any("Replaced 1 sentence that said more than the record" in e["what"] for e in history)


@pytest.mark.parametrize(("reply", "reason"), [
    ({"lbp_duration": "Pain for 12 weeks.", "conservative_care": "No therapy was tried.",
      "recent_xray": "An MRI was normal."}, "unsupported"),
    ("Low back pain, therapy and an X-ray are charted.", "unreadable"),
])
def test_when_no_sentence_holds_the_clinician_sees_the_records_words(env, reply, reason):
    svc, client, model, jobs = env
    model.sentences = reply if isinstance(reply, str) else dict(reply)
    client.post(f"/api/cases/{CASE}/request-attestation", headers=DOLORES)
    run(jobs)
    summary = client.get(f"/api/attestations/{CASE}", headers=REYES).json()
    assert summary["chart_summary_by"] == {"route": "template", "model": None, "fallback_reason": reason}
    assert summary["chart_summary"].startswith("Low back pain for at least six weeks: documented for 9 weeks")
    (written,) = [e for e in svc.audit.events(CASE) if e.action == "summary.written"]
    assert written.detail["fallback_reason"] == reason


def test_a_chart_that_documents_none_of_the_criteria_is_summarized_without_the_model(env):
    """With nothing documented, there is nothing for the model to summarize: the engine says what is unanswered."""
    svc, _, model, _ = env
    case = svc.cases.get(CASE)
    unknown = [r for r in case.effective_results() if r["status"] == "unknown"]
    case = case.model_copy(update={"determination": {**case.determination, "results": unknown}})
    model.sent.clear()
    summary = svc.workflow.drafter.chart_summary(case, svc.store.record(case.patient_id, case.id).patient,
                                                 [r["criterion_id"] for r in unknown])
    assert model.sent == [] and summary == {"text": UNANSWERED, "route": "template", "model": None}


def test_a_summary_that_comes_too_late_is_not_kept(env):
    svc, client, model, jobs = env
    client.post(f"/api/cases/{CASE}/request-attestation", headers=DOLORES)
    asked = client.get(f"/api/attestations/{CASE}", headers=REYES).json()["questions"]
    client.post(f"/api/attestations/{CASE}", headers=REYES, json={"answers": {q["criterion_id"]: "yes" for q in asked}})
    model.sent.clear()
    run(jobs)
    summary = svc.cases.get(CASE).chart_summary
    assert model.sent == [] and summary["route"] == "template" and summary["pending"] is False


def test_when_the_model_is_down_the_summary_from_the_facts_stays(env):
    svc, client, model, jobs = env
    model.down = True
    client.post(f"/api/cases/{CASE}/request-attestation", headers=DOLORES)
    run(jobs)
    assert client.get(f"/api/attestations/{CASE}", headers=REYES).json()["chart_summary_by"]["route"] == "template"
    (written,) = [e for e in svc.audit.events(CASE) if e.action == "summary.written"]
    assert written.detail["route"] == "template"


def test_with_no_external_model_drafts_are_written_inside_the_network_from_de_identified_facts(env):
    svc, client, model, _ = env
    draft = client.get(f"/api/cases/{CASE}", headers=DOLORES).json()["draft"]
    assert draft["route"] == "deid_in_vpc" and draft["sent"]["vendor"] == "Mesa Verde's own server"
    drafted = [m for m in model.sent if "Patient:" in m["messages"][1]["content"]]
    assert drafted and all("[NAME_" in m["messages"][1]["content"] for m in drafted)  # de-identified, even here


def test_the_summary_landing_while_the_clinician_signs_never_refuses_her_signature(env):
    """The model finishes while her signing request is still writing the draft. Her action holds the case from its
    read to its save, so the summary waits, finds she has answered, and is not kept; her signature stands."""
    svc, client, model, jobs = env
    client.post(f"/api/cases/{CASE}/request-attestation", headers=DOLORES)
    (job, args), = jobs
    summary = threading.Thread(target=job, args=args)
    real = svc.workflow.drafter.draft

    def draft_while_the_summary_lands(case, patient):
        summary.start()
        summary.join(timeout=1)  # it cannot save while she holds the case
        return real(case, patient)

    svc.workflow.drafter.draft = draft_while_the_summary_lands
    asked = client.get(f"/api/attestations/{CASE}", headers=REYES).json()["questions"]
    signed = client.post(f"/api/attestations/{CASE}", headers=REYES,
                         json={"answers": {q["criterion_id"]: "yes" for q in asked}})
    summary.join(timeout=10)
    assert signed.status_code == 200 and not summary.is_alive()
    case = svc.cases.get(CASE)
    assert case.state == "review" and case.chart_summary["route"] == "template"
    assert case.chart_summary["pending"] is False  # nothing is on its way any more
    actions = [e.action for e in svc.audit.events(CASE)]
    assert actions.count("attestation.signed") == 1 and "summary.written" not in actions


def test_a_summary_that_was_lost_is_not_waited_for(env):
    """A restart loses the background job. The clinician's screen stops saying a summary is coming after five
    minutes, and stops refreshing itself."""
    svc, client, _, jobs = env
    client.post(f"/api/cases/{CASE}/request-attestation", headers=DOLORES)
    jobs.clear()
    assert client.get(f"/api/attestations/{CASE}", headers=REYES).json()["chart_summary_pending"] is True
    svc.workflow.now = lambda: T0 + timedelta(minutes=6)
    assert client.get(f"/api/attestations/{CASE}", headers=REYES).json()["chart_summary_pending"] is False


def test_where_the_in_network_model_is_kept_for_summaries_a_switched_off_route_drafts_from_the_template(env):
    """The demo's model runs on a CPU: a draft on a request's path would outlast the request. With
    CADUCEUS_IN_VPC_DRAFTS=0, turning the external model off sends drafts to the template, not to that model."""
    svc, client, model, _ = env
    deid = svc.workflow.drafter._deid
    in_vpc = svc.router._in_vpc
    kept = Router(in_vpc, OpenAICompatibleProvider("http://outside/v1", "kimi-k2.6", client=httpx.Client(
        transport=httpx.MockTransport(lambda r: httpx.Response(500)))), deid, drafts_in_vpc=False)
    kept.switch_external(False)
    assert kept.describe()["drafts_in_vpc"] is False and kept.describe()["external_on"] is False
    model.sent.clear()
    case = svc.cases.get(CASE)
    draft = Drafter(kept, deid).draft(case, svc.store.record(case.patient_id, case.id).patient)
    assert draft["route"] == "template" and model.sent == []
