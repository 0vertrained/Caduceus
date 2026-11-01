"""Opening Caduceus from an EHR, through the HTTP API: the launch, the callback, the record filed in the visitor's own
copy of the demo, and a case started from it. A fake SMART server (tests/fake_smart.py) stands in for the EHR
through httpx.MockTransport, so no test calls the real launcher."""

from __future__ import annotations

import base64
import json
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from caduceus.api import create_app
from caduceus.app_factory import Settings, build_services
from caduceus.payer import build_request_bundle
from caduceus.ratelimit import Window
from caduceus.router.providers import ProviderUnavailable
from caduceus.seed import seed
from caduceus.smart import SANDBOX_ISS
from tests.conftest import DATA, TEST_KEY
from tests.fake_smart import PATIENT_ID, PRACTITIONER, FakeSmart, patient, record
from tests.test_workflow_api import DOLORES, REYES, T0, Clock

REDIRECT = "https://caduceus.example/smart/callback"
SA, SB = "a" * 32, "b" * 32
A, B = {"X-Caduceus-Sandbox": SA}, {"X-Caduceus-Sandbox": SB}
LUMBAR = {"service": "72148", "payer": "Medicare"}
CPAP = {"service": "E0601", "payer": "Medicare"}
PATEL = {"X-Caduceus-User": "dr.patel"}


def settings(fake: FakeSmart, **kw) -> Settings:
    return Settings(data_dir=DATA, db_path=":memory:",
                    **({"auth_mode": "demo", "smart_iss": fake.base, "smart_redirect_uris": REDIRECT} | kw))


@pytest.fixture()
def ehr():
    fake, clock = FakeSmart(as_of=T0.date()), Clock(T0)

    def fresh():
        svc = build_services(settings(fake))
        svc.workflow.now = clock
        seed(svc, now=clock())
        return svc

    client = TestClient(create_app(fresh(), dispatch_interval=None, fresh_demo=fresh, ehr_transport=fake.transport()))
    return SimpleNamespace(client=client, fake=fake, clock=clock, live=client.app.state.live)


def copy(t, sandbox: str = SA):
    """A visitor's own copy of the demo, as the API holds it."""
    return t.live._sandboxes[sandbox].svc


def start(t, sandbox: dict = A, iss: str | None = None, who: dict = DOLORES):
    return t.client.post("/api/smart/launch", headers=who | sandbox,
                         json={"iss": iss or t.fake.base, "launch": "ctx-42", "redirect_uri": REDIRECT})


def callback(t, code: str, state: str, sandbox: dict = A):
    return t.client.post("/api/smart/callback", headers=DOLORES | sandbox, json={"code": code, "state": state})


def opened(t, sandbox: dict = A) -> str:
    """The whole launch, as the browser takes it: start, the EHR's authorize page, back with a code. Returns the
    record's key."""
    r = start(t, sandbox)
    assert r.status_code == 200, r.text
    code, state = t.fake.authorize(r.json()["authorize_url"])
    r = callback(t, code, state, sandbox)
    assert r.status_code == 200, r.text
    return r.json()["record"]


def new_case(t, key: str, body: dict = LUMBAR, sandbox: dict = A):
    return t.client.post(f"/api/smart/records/{key}/case", headers=DOLORES | sandbox, json=body)


def refusals(t, sandbox: str = SA) -> list[str]:
    return [e.detail["reason"] for e in copy(t, sandbox).audit.events() if e.action == "ehr.launch_refused"]


def test_a_launch_opens_the_patients_record_and_says_what_it_holds(ehr):
    key = opened(ehr)
    view = ehr.client.get(f"/api/smart/records/{key}", headers=DOLORES | A).json()
    assert view["patient"] == {"name": "Evelyn Mares", "born": "April 12, 1957", "age": 69, "sex": "Female"}
    assert view["source"]["host"] == "ehr.example" and view["source"]["by"] == "Dolores Montoya"
    assert [(h["what"], h["words"]) for h in view["holds"]] == [
        # The CPAP criteria accept a sleep test, and its index, from any date, so both are read whole.
        ("Diagnoses", "6 on the record, 5 of them active."), ("Procedures", "5 in the whole record."),
        ("Measurements and test results", "28 in the whole record."),
        ("Notes", "The 5 newest, which become the case’s pages.")]
    assert view["relevant"] == ["Essential hypertension, on the record since September 20, 2008",
                                "Chronic low back pain, on the record since March 14, 2019",
                                "Osteoarthritis of knee, on the record since June 2, 2021"]
    services = {s["code"]: s for s in view["services"]}
    # Physical therapy three years ago is read now, and is too long ago for lumbar MRI: not met, not unknown.
    assert (services["72148"]["met"], services["72148"]["unmet"], services["72148"]["total"]) == (1, 1, 4)
    assert services["72148"]["answers"] == ("The record answers 2 of the 4 criteria. One is not met, so the request "
                                            "cannot be approved as it stands. Dr. Ana Reyes will be asked to confirm "
                                            "the other 2.")
    assert services["E0601"]["answers"] == ("The record answers 1 of the 4 criteria. Dr. Priya Patel will be asked "
                                            "to confirm the other 3.")
    assert services["27447"]["side"] == "knee" and services["27447"]["met"] == 2
    assert view["payers"] == ["Humana Medicare Advantage", "Medicaid managed care", "Medicare",
                              "UnitedHealthcare Medicare Advantage"]
    assert view["cases"] == [] and PATIENT_ID not in json.dumps(view)  # the EHR's id for the patient stays inside


def test_an_iss_off_the_allowlist_is_refused_and_recorded_before_anything_is_fetched(ehr):
    r = start(ehr, iss="https://evil.example/v/r4/fhir")
    assert r.status_code == 400 and "not set up to open from that EHR" in r.json()["message"]
    assert ehr.fake.requests == []
    assert [e.detail for e in copy(ehr).audit.events() if e.action == "ehr.launch_refused"] == [
        {"reason": "iss_not_allowed", "iss": "https://evil.example/v/r4/fhir"}]


def test_a_state_is_good_once_and_for_ten_minutes(ehr):
    code, state = ehr.fake.authorize(start(ehr).json()["authorize_url"])
    assert callback(ehr, code, state).status_code == 200
    again = callback(ehr, code, state)
    assert again.status_code == 400 and "already been used" in again.json()["message"]
    code, state = ehr.fake.authorize(start(ehr).json()["authorize_url"])
    ehr.clock.advance(minutes=11)
    late = callback(ehr, code, state)
    assert late.status_code == 400 and "took longer than 10 minutes" in late.json()["message"]
    assert ehr.fake.hits("/token") == 1 and refusals(ehr) == ["state_unknown", "state_expired"]


def test_a_launch_started_in_one_copy_cannot_finish_in_another(ehr):
    code, state = ehr.fake.authorize(start(ehr, A).json()["authorize_url"])
    assert callback(ehr, code, state, B).status_code == 400
    assert ehr.fake.hits("/token") == 0 and refusals(ehr, SB) == ["state_unknown"]
    assert callback(ehr, code, state, A).status_code == 400  # and it is spent


def test_the_ehr_refusing_the_code_is_said_in_plain_words(ehr):
    ehr.fake.token_status = 400
    code, state = ehr.fake.authorize(start(ehr).json()["authorize_url"])
    r = callback(ehr, code, state)
    assert r.status_code == 502 and r.json() == {"message": "The EHR did not accept the sign-in. Open Caduceus "
                                                            "from the EHR again."}
    assert copy(ehr).workflow.launched.keys_oldest_first() == [] and refusals(ehr) == ["token_refused"]


def test_the_record_lives_only_in_the_visitors_copy_and_goes_when_it_starts_over(ehr):
    key = opened(ehr, A)
    assert ehr.client.get(f"/api/smart/records/{key}", headers=DOLORES | A).status_code == 200
    assert ehr.client.get(f"/api/smart/records/{key}", headers=DOLORES | B).status_code == 404
    assert ehr.client.get(f"/api/smart/records/{key}", headers=DOLORES).status_code == 404  # nor the shared set
    assert new_case(ehr, key, sandbox=B).status_code == 404
    ehr.clock.advance(minutes=2)
    assert ehr.client.post("/api/demo/reset", headers=DOLORES | A).status_code == 200
    assert ehr.client.get(f"/api/smart/records/{key}", headers=DOLORES | A).status_code == 404
    key = opened(ehr, A)
    ehr.clock.advance(hours=7)
    ehr.live.expire()
    assert ehr.client.get(f"/api/smart/records/{key}", headers=DOLORES | A).status_code == 404


def test_a_case_from_the_record_goes_through_the_same_workflow(ehr):
    # CPAP, whose criteria this record can only partly answer and nothing in it fails: the rest is the clinician's.
    key = opened(ehr)
    assert new_case(ehr, key, CPAP).json() == {"case_id": "EHR-0001"}
    c = ehr.client.get("/api/cases/EHR-0001", headers=DOLORES | A).json()
    assert c["state"] == "review" and c["criteria"]["counts"] == {"met": 1, "unmet": 0, "unknown": 3}
    assert c["came_from_ehr"]["words"] == ("Opened from the EHR at ehr.example by a SMART launch. Dolores Montoya "
                                           "started the case today at 1:00 pm, from the record the EHR shared today "
                                           "at 1:00 pm.")
    assert c["payer"] == {"name": "Medicare", "member_id_last4": ""} and len(c["packet"]) == 5
    assert c["ordering_clinician"]["name"] == "Dr. Priya Patel" and c["draft"]["route"] == "template"
    page = ehr.client.get(f"/api/cases/EHR-0001/pages/{c['packet'][0]['id']}", headers=DOLORES | A).json()
    assert page["title"] == "Progress note" and "chronic low back pain" in page["text"].lower()
    rows = [r["id"] for g in ehr.client.get("/api/queue", headers=DOLORES | A).json()["groups"] for r in g["cases"]]
    assert "EHR-0001" in rows

    # What the record does not answer goes to the ordering clinician; then approval, the outbox and the payer.
    ehr.client.post("/api/cases/EHR-0001/request-attestation", headers=DOLORES | A)
    asked = ehr.client.get("/api/attestations/EHR-0001", headers=PATEL | A).json()["questions"]
    ehr.client.post("/api/attestations/EHR-0001", headers=PATEL | A,
                    json={"answers": {q["criterion_id"]: "yes" for q in asked}})
    assert ehr.client.post("/api/cases/EHR-0001/approve", headers=DOLORES | A).json()["state"] == "submitted"
    ehr.clock.advance(seconds=31)
    assert ehr.client.get("/api/cases/EHR-0001", headers=DOLORES | A).json()["state"] == "approved"

    received = next(e for e in copy(ehr).audit.events("EHR-0001") if e.action == "case.received")
    assert received.actor == "dolores" and received.detail == {
        "service": "E0601", "pages": 5, "source": "smart_launch", "iss": ehr.fake.base, "record": key}
    history = ehr.client.get("/api/cases/EHR-0001/timeline", headers=DOLORES | A).json()["entries"]
    assert history[0]["what"] == ("Opened Caduceus from the EHR at ehr.example by a SMART launch. The EHR shared the "
                                  "patient’s record: 6 diagnoses, 5 procedures, 28 measurements and results and 5 "
                                  "notes. Its access token was used for that one read and kept nowhere.")
    assert any(e["what"].startswith("Started the case from the record the EHR at ehr.example shared") for e in history)


def test_a_knee_replacement_names_the_knee_through_to_the_payer(ehr):
    key = opened(ehr)
    no_side = new_case(ehr, key, {"service": "27447", "payer": "Medicare"})
    assert no_side.status_code == 409 and no_side.json()["field"] == "side"
    assert no_side.json()["message"] == "Choose which knee the request is for."
    assert new_case(ehr, key, {"service": "27447", "side": "both", "payer": "Medicare"}).status_code == 422
    case_id = new_case(ehr, key, {"service": "27447", "side": "left", "payer": "Medicare"}).json()["case_id"]
    c = ehr.client.get(f"/api/cases/{case_id}", headers=DOLORES | A).json()
    assert c["side"] == "left" and c["service"]["display"] == "Total knee replacement"
    # The record names no side for its knee arthritis, and its therapy was three years ago.
    assert c["criteria"]["counts"] == {"met": 2, "unmet": 1, "unknown": 3}
    item = build_request_bundle(copy(ehr).cases.get(case_id))["entry"][0]["resource"]["item"][0]
    assert item["modifier"][0]["coding"][0]["code"] == "LT"
    assert new_case(ehr, key, {"service": "27447", "side": "right", "payer": "Medicare"}).status_code == 200


def test_a_second_request_for_the_same_service_is_refused_even_after_opening_the_patient_again(ehr):
    key = opened(ehr)
    assert new_case(ehr, key).status_code == 200
    again = new_case(ehr, key)
    assert again.status_code == 409 and again.json()["case_id"] == "EHR-0001"
    fresh = opened(ehr)  # read afresh under a key of its own: EHR-0001 keeps the record it was checked against
    assert fresh != key and copy(ehr).workflow.launched.get(key) is not None
    assert new_case(ehr, fresh).json()["case_id"] == "EHR-0001"  # still the same patient
    assert new_case(ehr, fresh, {"service": "E0601", "payer": "Medicare"}).json() == {"case_id": "EHR-0002"}
    view = ehr.client.get(f"/api/smart/records/{fresh}", headers=DOLORES | A).json()
    assert [c["id"] for c in view["cases"]] == ["EHR-0001", "EHR-0002"]
    assert opened(ehr) != fresh  # a case uses this one too
    key = fresh
    unknown = new_case(ehr, key, {"service": "99999", "payer": "Medicare"})
    assert unknown.status_code == 409 and unknown.json()["field"] == "service"
    assert new_case(ehr, key, {"service": "72148", "payer": "Aetna"}).json()["field"] == "payer"


def test_a_copy_holds_three_patients_from_the_ehr(ehr):
    def another(pid: str) -> str:
        ehr.fake.patient_resource, ehr.fake.resources = patient(pid), record(T0.date(), pid)
        return opened(ehr)

    first = another("patient-1")
    another("patient-2")
    third = another("patient-3")
    another("patient-4")  # no case uses the first, so it goes to make room
    assert ehr.client.get(f"/api/smart/records/{first}", headers=DOLORES | A).status_code == 404
    for key in copy(ehr).workflow.launched.keys_oldest_first():
        assert new_case(ehr, key).status_code == 200
    code, state = ehr.fake.authorize(start(ehr).json()["authorize_url"])
    ehr.fake.patient_resource, ehr.fake.resources = patient("patient-5"), record(T0.date(), "patient-5")
    full = callback(ehr, code, state)
    assert full.status_code == 409 and "already holds 3 patients opened from an EHR" in full.json()["message"]
    assert third in copy(ehr).workflow.launched.keys_oldest_first()
    let_go = [e.detail for e in copy(ehr).audit.events() if e.action == "ehr.launch_refused"][-1]
    assert let_go == {"reason": "not_filed", "iss": ehr.fake.base,
                      "discarded": {"Condition": 6, "Procedure": 5, "Observation": 28, "DocumentReference": 8}}


def test_a_record_the_engine_cannot_read_is_not_filed(ehr):
    odd = {"resourceType": "Condition", "id": "odd", "subject": {"reference": f"Patient/{PATIENT_ID}"},
           "code": ["not", "a", "concept"]}
    ehr.fake.resources = record(T0.date()) + [odd]
    code, state = ehr.fake.authorize(start(ehr).json()["authorize_url"])
    r = callback(ehr, code, state)
    assert r.status_code == 502 and "could not read" in r.json()["message"]
    assert copy(ehr).workflow.launched.keys_oldest_first() == []
    let_go = [e.detail for e in copy(ehr).audit.events() if e.action == "ehr.launch_refused"][-1]
    assert (let_go["reason"], let_go["step"], let_go["discarded"]["Condition"]) == ("bad_response", "record", 7)


def test_nothing_secret_reaches_the_audit_log_the_record_or_the_browser(ehr):
    shown = [start(ehr)]
    pending = next(iter(ehr.client.app.state.smart._pending.values()))
    verifier, state = pending.verifier, pending.state
    code, _ = ehr.fake.authorize(shown[0].json()["authorize_url"])
    shown.append(callback(ehr, code, state))
    key = shown[-1].json()["record"]
    shown += [ehr.client.get(f"/api/smart/records/{key}", headers=DOLORES | A), new_case(ehr, key),
              ehr.client.get("/api/cases/EHR-0001", headers=DOLORES | A),
              ehr.client.get("/api/cases/EHR-0001/timeline", headers=DOLORES | A),
              ehr.client.get("/api/cases/EHR-0001/history", headers=DOLORES | A)]
    svc = copy(ehr)
    logged = json.dumps([e.detail for e in svc.audit.events()])
    filed = json.dumps(svc.workflow.launched.get(key))
    secrets = ehr.fake.issued + [verifier]  # the code, the access token, the ID token; and the PKCE verifier
    assert len(secrets) == 4
    for secret in secrets:
        assert secret not in logged and secret not in filed and not any(secret in r.text for r in shown)
    assert state not in logged and state not in filed
    read = next(e for e in svc.audit.events() if e.action == "ehr.record_read")
    assert read.detail["iss"] == ehr.fake.base and read.detail["ehr_user"] == PRACTITIONER
    assert set(read.detail) == {"iss", "resources", "notes", "not_shared", "dropped", "requests", "bytes", "scope",
                                "ehr_user"}
    assert ehr.client.app.state.smart.pending() == 0  # nothing about the launch outlives it


def test_launch_steps_are_limited_per_visitor(ehr):
    ehr.client.app.state.demo_limits.ehr_launches = Window(2, 300, lambda: 0.0)
    responses = [start(ehr) for _ in range(3)]
    assert [r.status_code for r in responses] == [200, 200, 429]
    assert "Too many launches" in responses[-1].json()["detail"] and responses[-1].headers["Retry-After"] == "300"


def test_bodies_are_bounded_and_a_launch_needs_a_specialist_in_their_own_copy(ehr):
    assert start(ehr, iss="https://ehr.example/" + "v" * 600).status_code == 422
    assert callback(ehr, "c" * 2049, "s").status_code == 422
    assert callback(ehr, "c", "s" * 65).status_code == 422
    assert callback(ehr, "", "s").status_code == 422
    r = ehr.client.post("/api/smart/launch", headers=DOLORES, json={"iss": ehr.fake.base, "launch": "x",
                                                                    "redirect_uri": REDIRECT})
    assert r.status_code == 400 and r.json()["detail"] == "Open this page from the Caduceus demo site."
    assert start(ehr, who=REYES).status_code == 403
    assert start(ehr, who={}).status_code == 401
    assert ehr.fake.requests == []


def test_the_launch_is_off_outside_the_demo_unless_it_is_set_up(monkeypatch):
    for name in ("CADUCEUS_SMART_ISS", "CADUCEUS_SMART_REDIRECT_URIS", "CADUCEUS_SMART_CLIENT_ID"):
        monkeypatch.delenv(name, raising=False)
    dev = TestClient(create_app(build_services(Settings(data_dir=DATA, db_path=":memory:", auth_mode="dev")),
                                dispatch_interval=None))
    assert dev.get("/api/smart").json() == {"enabled": False, "client_id": None}
    assert dev.post("/api/smart/launch", headers=DOLORES,
                    json={"iss": SANDBOX_ISS[0], "launch": "x", "redirect_uri": REDIRECT}).status_code == 404
    demo = create_app(build_services(Settings(data_dir=DATA, db_path=":memory:", auth_mode="demo",
                                              smart_redirect_uris=REDIRECT)), dispatch_interval=None)
    assert TestClient(demo).get("/api/smart").json() == {"enabled": True, "client_id": "caduceus-demo"}
    assert demo.state.smart.allowed == frozenset(SANDBOX_ISS)


def test_a_launched_case_drafts_from_de_identified_facts_and_falls_back_to_the_template():
    fake = FakeSmart(as_of=T0.date())
    svc = build_services(Settings(data_dir=DATA, db_path=":memory:", auth_mode="dev", external_provider="stub",
                                  deid_key=TEST_KEY.decode(), smart_iss=fake.base, smart_redirect_uris=REDIRECT))
    svc.workflow.now = Clock(T0)
    t = SimpleNamespace(client=TestClient(create_app(svc, dispatch_interval=None, ehr_transport=fake.transport())),
                        fake=fake)
    key = opened(t, sandbox={})  # outside the demo there are no copies
    c = t.client.get(f"/api/cases/{new_case(t, key, sandbox={}).json()['case_id']}", headers=DOLORES).json()
    assert c["draft"]["route"] == "external" and "Mares" in c["draft"]["text"]
    sent = c["draft"]["sent"]["text"]
    for identifier in ("Evelyn", "Mares", "1957", "MR-55821934", "999-41-5521", "Albuquerque", "87106",
                       "505-555-0144", "March 14, 2019"):
        assert identifier not in sent

    def down(*_args, **_kw):
        raise ProviderUnavailable("external provider unavailable: ReadTimeout")

    svc.router._external._inner.complete = down  # the model times out: the draft comes from the template
    c = t.client.get(f"/api/cases/{new_case(t, key, {'service': 'E0601', 'payer': 'Medicare'}, {}).json()['case_id']}",
                     headers=DOLORES).json()
    assert c["state"] == "review" and c["draft"]["route"] == "template"
    assert c["draft"]["fallback_reason"].startswith("ProviderUnavailable")


def test_the_outcomes_count_records_read_from_an_ehr(ehr):
    opened(ehr)
    sections = ehr.client.get("/api/outcomes", headers=DOLORES | A).json()["sections"]
    row = next(r for s in sections for r in s["rows"] if r["label"] == "Records read from an EHR")
    assert row["value"] == "1"



def test_opening_the_patient_again_never_changes_the_record_a_case_was_made_from(ehr):
    """The EHR gains a note, and the specialist opens the patient again. The case keeps the pages it was checked
    against, and the new note is in the new read. A record no case uses is simply replaced."""
    key = opened(ehr)
    case = new_case(ehr, key).json()["case_id"]
    page_one = ehr.client.get(f"/api/cases/{case}/pages/{key}-1", headers=DOLORES | A).json()["text"]
    ehr.fake.resources.append({
        "resourceType": "DocumentReference", "id": "doc-rash", "subject": {"reference": f"Patient/{PATIENT_ID}"},
        "status": "current", "date": f"{T0.date().isoformat()}T09:00:00-06:00", "type": {"text": "Telephone note"},
        "content": [{"attachment": {"contentType": "text/plain",
                                    "data": base64.b64encode(b"Called about an unrelated rash.").decode()}}]})
    fresh = opened(ehr)
    assert fresh != key
    assert ehr.client.get(f"/api/cases/{case}/pages/{key}-1", headers=DOLORES | A).json()["text"] == page_one
    newest = copy(ehr).store.document(f"{fresh}-1")
    assert "unrelated rash" in base64.b64decode(newest["content"][0]["attachment"]["data"]).decode()
    assert opened(ehr) == opened(ehr)  # no case uses the newest read: it is replaced, under the same key


def test_a_launch_is_refused_before_anything_is_fetched_where_no_record_can_be_kept(ehr):
    """The Postgres store keeps no records from an EHR yet. A launch there must stop before it asks the EHR for
    anything, not after reading a whole record only to drop it."""
    assert ehr.client.get("/api/queue", headers=DOLORES | A).status_code == 200  # the visitor's copy exists
    copy(ehr).workflow.launched = None
    refused = start(ehr)
    assert refused.status_code == 404 and ehr.fake.requests == []
