"""Faxed referrals: read inside the boundary, low-confidence fields checked by a person, then a case."""

from __future__ import annotations

import json
import shutil

import pytest
from fastapi.testclient import TestClient

from caduceus.api import create_app
from caduceus.app_factory import Settings, build_services
from caduceus.auth import AGENT
from caduceus.intake import check, read_fax
from caduceus.seed import seed
from caduceus.workflow import ActionRefused
from tests.conftest import DATA
from tests.test_workflow_api import DOLORES, REYES, T0, Clock, as_user

pytestmark = pytest.mark.skipif(shutil.which("tesseract") is None, reason="Tesseract OCR is not installed")
MANIFEST = json.loads((DATA / "faxes" / "faxes.json").read_text())


@pytest.fixture()
def inbox():
    svc = build_services(Settings(data_dir=DATA, db_path=":memory:", auth_mode="dev"))
    svc.workflow.now = Clock(T0)
    seed(svc, now=T0, by_fax=True)
    return svc, TestClient(create_app(svc, dispatch_interval=None))


def test_no_wrong_field_is_accepted_without_a_person():
    known = {"payers": {"Medicare", "UnitedHealthcare Medicare Advantage"}, "codes": {"E0601", "27447", "72148"},
             "clinicians": {"Dr. Ana Reyes", "Dr. Marcus Lee", "Dr. Priya Patel"}}
    for meta in MANIFEST.values():
        fields = check(read_fax(DATA / "faxes" / meta["file"])["fields"], known)
        wrong_and_accepted = [f["key"] for f in fields
                              if f["value"] != meta["expected"][f["key"]] and not f["problem"]]
        assert wrong_and_accepted == [], meta["quality"]
        routed = sum(1 for f in fields if f["problem"])
        assert (routed == 0) if meta["quality"] != "degraded" else (routed >= 5)


def test_a_clean_fax_becomes_a_case_once_a_person_confirms_it(inbox):
    svc, client = inbox
    assert len(svc.cases.all()) == 8
    faxes = client.get("/api/intake", headers=DOLORES).json()
    assert faxes["available"] and [(f["status"], f["to_check"]) for f in faxes["faxes"]][:2] == [("read", 0)] * 2
    item = client.get("/api/intake/FAX-0001", headers=DOLORES).json()
    values = {f["key"]: f["value"] for f in item["fields"]}
    assert values["patient_name"] == "Federico Gurule" and all(f["box"] for f in item["fields"])
    image = client.get(item["image"], headers=DOLORES)
    assert image.status_code == 200 and image.headers["content-type"] == "image/png"

    as_auditor = client.post("/api/intake/FAX-0001/confirm", headers=as_user("sam"), json={"values": values})
    assert as_auditor.status_code == 409
    made = client.post("/api/intake/FAX-0001/confirm", headers=DOLORES, json={"values": values}).json()
    assert made == {"case_id": "2026-1023"}
    case = client.get("/api/cases/2026-1023", headers=DOLORES).json()
    assert case["state"] == "review" and case["patient"]["name"] == "Federico Gurule"
    assert case["payer"]["member_id_last4"] == "6782" and case["service"]["code"] == "E0601"
    again = client.post("/api/intake/FAX-0001/confirm", headers=DOLORES, json={"values": values})
    assert again.status_code == 409 and "already been turned into a case" in again.json()["message"]
    assert {"fax.read", "fax.viewed", "fax.confirmed"} <= {e.action for e in svc.audit.events("FAX-0001")}


def test_a_degraded_fax_needs_its_fields_corrected_first(inbox):
    svc, client = inbox
    item = client.get("/api/intake/FAX-0003", headers=DOLORES).json()
    as_read = {f["key"]: f["value"] for f in item["fields"]}
    flagged = [f for f in item["fields"] if f["problem"]]
    assert len(flagged) >= 5 and all(f["problem"].endswith(".") for f in flagged)
    refused = client.post("/api/intake/FAX-0003/confirm", headers=DOLORES, json={"values": as_read})
    assert refused.status_code == 409 and refused.json()["message"].startswith("Check the ")

    # Unsure, and either read right or started on the right choice: a person ticks each as matching the fax.
    ticks = ["referral", "patient_name", "payer", "clinician", "service_code"]
    wrong_person = MANIFEST["FAX-0003"]["expected"] | {"birth_date": "01/01/1941"}
    refused = client.post("/api/intake/FAX-0003/confirm", headers=DOLORES,
                          json={"values": wrong_person, "checked": ticks})
    assert refused.status_code == 409 and "No patient named Shelba Gulgowski" in refused.json()["message"]

    fixed = MANIFEST["FAX-0003"]["expected"]
    assert client.post("/api/intake/FAX-0003/confirm", headers=DOLORES,
                       json={"values": fixed, "checked": ticks}).json() == {"case_id": "2026-1029"}
    confirmed = next(e for e in svc.audit.events("FAX-0003") if e.action == "fax.confirmed")
    assert {"member_id", "clinician", "service_code"} <= set(confirmed.detail["corrected"])
    assert svc.cases.get("2026-1029").effective_counts()["unmet"] == 1  # the same finding as the seeded case


def test_the_form_follows_the_page_and_starts_on_the_closest_choice_on_file(inbox):
    _, client = inbox
    item = client.get("/api/intake/FAX-0003", headers=DOLORES).json()
    assert [f["key"] for f in item["form"]] == ["referral", "patient_name", "birth_date", "member_id", "payer",
                                                "clinician", "service_code", "diagnosis"]
    form = {f["key"]: f for f in item["form"]}
    # The "From:" line names the clinic, which narrows the clinicians to the ones who work there.
    assert item["clinic"] == "Mesa Verde Orthopedics, Santa Fe" and "Santa" in item["sender"]["read_as"]
    clinician = form["clinician"]
    assert clinician["kind"] == "select" and clinician["at_clinic"] == ["Dr. Ana Reyes"]
    assert clinician["read_as"] == "Dr Ana Reyes" and clinician["initial"] == "Dr. Ana Reyes" and clinician["problem"]
    # One choice for the service, by its code; both lines the fax gave are shown as read.
    service = form["service_code"]
    assert service["label"] == "Requested service" and service["initial"] == "72148"
    assert service["read_as"] == "72148 one, MRI of the lumbar spine without contrast"
    assert {"code": "72148", "label": "MRI of the lumbar spine without contrast (CPT 72148)"} in \
        item["choices"]["services"]
    assert form["payer"]["initial"] == "UnitedHealthcare Medicare Advantage"
    assert form["birth_date"] == form["birth_date"] | {"kind": "date", "initial": "03/09/1941"}
    assert form["member_id"]["kind"] == "text" and form["member_id"]["initial"] == "mvo0s4522654"
    assert item["to_check"] == sum(1 for f in item["form"] if f["problem"]) == 7

    clean = client.get("/api/intake/FAX-0001", headers=DOLORES).json()
    assert clean["clinic"] == "Mesa Verde Sleep Center" and not any(f["problem"] for f in clean["form"])
    assert {f["key"]: f["initial"] for f in clean["form"]}["clinician"] == "Dr. Priya Patel"


def form_start(client, fax_id: str) -> dict[str, str]:
    """The values the check-the-fax form starts with, as the screen sends them back untouched."""
    item = client.get(f"/api/intake/{fax_id}", headers=DOLORES).json()
    return {f["key"]: f["initial"] for f in item["form"]}


def test_every_unsure_field_is_ticked_or_corrected_before_the_case_exists(inbox):
    svc, client = inbox
    values = form_start(client, "FAX-0003") | {"member_id": "MV084522654",
                                                 "diagnosis": "M54.50 Low back pain, unspecified"}

    def confirm(checked: list[str]):
        return client.post("/api/intake/FAX-0003/confirm", headers=DOLORES, json={"values": values,
                                                                                   "checked": checked})

    refused = confirm([])
    assert refused.status_code == 409 and refused.json()["field"] == "referral"
    assert refused.json()["message"] == ("Check the referral number against the fax, then tick “Matches the fax”, "
                                         "or correct it.")
    # A choice the form started on is not a person's choice: the suggested clinician still needs its tick.
    refused = confirm(["referral", "patient_name", "payer", "service_code"])
    assert refused.json()["field"] == "clinician" and svc.cases.get("2026-1029") is None
    # A tick for a field that was read clearly, or for one that does not exist, counts for nothing.
    assert confirm(["birth_date", "nonsense", "referral", "patient_name", "payer", "service_code"]).json()["field"] \
        == "clinician"
    assert client.post("/api/intake/FAX-0003/confirm", headers=DOLORES,
                       json={"values": values, "checked": ["x" * 41]}).status_code == 422

    made = confirm(["referral", "patient_name", "payer", "clinician", "service_code"])
    assert made.json() == {"case_id": "2026-1029"}
    confirmed = next(e for e in svc.audit.events("FAX-0003") if e.action == "fax.confirmed")
    assert confirmed.detail["ticked"] == ["clinician", "patient_name", "payer", "referral", "service_code"]
    assert set(confirmed.detail["checked"]) == {"referral", "patient_name", "member_id", "payer", "clinician",
                                                "service_code", "diagnosis"}


def test_the_service_is_chosen_by_its_code_and_named_from_the_catalog(inbox):
    svc, client = inbox
    values = form_start(client, "FAX-0001")
    unknown = client.post("/api/intake/FAX-0001/confirm", headers=DOLORES,
                          json={"values": values | {"service_code": "99999"}})
    assert unknown.status_code == 409 and unknown.json()["field"] == "service_code"
    assert unknown.json()["message"].startswith("Check the requested service:")
    made = client.post("/api/intake/FAX-0001/confirm", headers=DOLORES,
                       json={"values": values | {"service": "An MRI of the brain, with contrast"}})
    assert made.status_code == 200
    assert svc.cases.get("2026-1023").service["display"] == "CPAP device for sleep apnea"


def test_the_patient_is_found_in_the_chart_or_the_closest_are_offered(inbox):
    svc, client = inbox

    def look(name: str, born: str, ref: str | None = None, who: dict = DOLORES):
        return client.post("/api/intake/FAX-0003/match", headers=who,
                           json={"name": name, "birth_date": born} | ({"ref": ref} if ref else {}))

    assert look("Shelba Gulgowski", "03/09/1941").status_code == 409  # not read yet: nothing to check
    client.get("/api/intake/FAX-0003", headers=DOLORES)
    found = look("Shelba Gulgowski", "03/09/1941").json()
    assert found["status"] == "matched" and found["candidates"] == []
    assert found["patient"] | {"ref": None} == {"ref": None, "name": "Shelba Gulgowski", "born": "March 9, 1941",
                                                "birth_date": "03/09/1941", "city": "Santa Fe"}
    # The plan on file comes with the match, so the screen can compare it with the fax; only its last 4 digits.
    assert found["on_file"] == {"payer": "UnitedHealthcare Medicare Advantage", "member_id_last4": "2654"}
    patient_id = svc.cases.get("2026-1043").patient_id  # a record number never leaves: not this one, not hers
    shelba = svc.store.find_patient("Shelba Gulgowski", "1941-03-09")
    assert shelba not in look("Shelba Gulgowski", "03/09/1941").text and patient_id not in found["patient"]["ref"]

    # The wrong date of birth: no exact match, so the closest records are offered, and one can be chosen.
    near = look("Shelba Gulgowsky", "01/01/1941").json()
    assert near["status"] == "candidates" and near["patient"] is None
    assert [c["name"] for c in near["candidates"]] == ["Shelba Gulgowski"]
    chosen = look("Shelba Gulgowsky", "01/01/1941", near["candidates"][0]["ref"]).json()
    assert chosen["status"] == "chosen" and chosen["patient"]["birth_date"] == "03/09/1941"
    assert look("Shelba Gulgowsky", "01/01/1941", "0" * 16).status_code == 409
    nobody = look("Jon Smith", "01/01/1950").json()
    assert nobody == {"status": "none", "patient": None, "on_file": None, "candidates": []}

    # Only a PA specialist looks patients up; names travel in the body, bounded, and are not kept in the audit log.
    assert look("Shelba Gulgowski", "03/09/1941", who=REYES).status_code == 403
    assert look("Shelba Gulgowski", "03/09/1941", who=as_user("sam")).status_code == 403
    assert look("x" * 121, "03/09/1941").status_code == 422
    searches = [e for e in svc.audit.events("FAX-0003") if e.action == "fax.patient_searched"]
    assert [e.detail["result"] for e in searches] == ["matched", "matched", "candidates", "chosen", "none"]
    assert not any("Shelba" in str(e.detail) or "Gulgow" in str(e.detail) for e in searches)


def test_the_clinics_match_the_ones_the_cover_sheets_are_drawn_with():
    import ast

    from caduceus.app_factory import CLINICS
    source = ast.parse((DATA.parent / "scripts" / "make_faxes.py").read_text())
    drawn = next(ast.literal_eval(n.value) for n in source.body
                 if isinstance(n, ast.Assign) and getattr(n.targets[0], "id", None) == "CLINICS")
    assert drawn == CLINICS


def test_the_fax_tool_reads_only_the_intake_folder(inbox):
    svc, _ = inbox
    with pytest.raises(ActionRefused, match="no such fax"):
        svc.workflow.gateway.call(AGENT, "FAX-0001", "intake", "documents.read_fax", fax_file="../cases/cases.json")
