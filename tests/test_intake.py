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
from tests.test_workflow_api import DOLORES, T0, Clock, as_user

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

    wrong_person = MANIFEST["FAX-0003"]["expected"] | {"birth_date": "01/01/1941"}
    refused = client.post("/api/intake/FAX-0003/confirm", headers=DOLORES, json={"values": wrong_person})
    assert refused.status_code == 409 and "No patient named Shelba Gulgowski" in refused.json()["message"]

    fixed = MANIFEST["FAX-0003"]["expected"]
    assert client.post("/api/intake/FAX-0003/confirm", headers=DOLORES, json={"values": fixed}).json() == {
        "case_id": "2026-1029"}
    confirmed = next(e for e in svc.audit.events("FAX-0003") if e.action == "fax.confirmed")
    assert {"member_id", "clinician", "service_code"} <= set(confirmed.detail["corrected"])
    assert svc.cases.get("2026-1029").effective_counts()["unmet"] == 1  # the same finding as the seeded case


def test_the_fax_tool_reads_only_the_intake_folder(inbox):
    svc, _ = inbox
    with pytest.raises(ActionRefused, match="no such fax"):
        svc.workflow.gateway.call(AGENT, "FAX-0001", "intake", "documents.read_fax", fax_file="../cases/cases.json")
