"""A sided request (a knee replacement is for one knee) names its side, read from the fax and confirmed by a person."""

from __future__ import annotations

import shutil

import pytest

from caduceus.intake import side_field
from tests.test_intake import MANIFEST, form_start, inbox  # noqa: F401  (the fixture)
from tests.test_workflow_api import DOLORES


def test_the_side_is_read_from_the_diagnosis_and_asked_for_when_it_is_missing():
    named = side_field({"value": "M17.11 Primary osteoarthritis, right knee", "confidence": 0.95, "box": None},
                       ["27447"])
    assert named["initial"] == "right" and named["problem"] is None and named["for_services"] == ["27447"]
    unnamed = side_field({"value": "M17.9 Osteoarthritis of knee", "confidence": 0.95, "box": None}, ["27447"])
    assert unnamed["initial"] == "" and "does not say which side" in unnamed["problem"]


@pytest.mark.skipif(shutil.which("tesseract") is None, reason="Tesseract OCR is not installed")
def test_a_knee_case_is_created_only_with_its_side(inbox):  # noqa: F811
    svc, client = inbox
    svc.workflow.catalog["services"]["27447"]["needs_side"] = True  # as when the active knee set checks the side
    item = client.get("/api/intake/FAX-0002", headers=DOLORES).json()
    side = next(f for f in item["form"] if f["key"] == "side")
    assert side["initial"] == "right"  # "M17.11 Primary osteoarthritis, right knee"
    expected = MANIFEST["FAX-0002"]["expected"]
    values = {k: expected.get(k, v) for k, v in form_start(client, "FAX-0002").items()}
    checked = [f["key"] for f in item["form"] if f["problem"]]

    refused = client.post("/api/intake/FAX-0002/confirm", headers=DOLORES,
                          json={"values": values | {"side": ""}, "checked": checked})
    assert refused.status_code == 409 and refused.json()["field"] == "side"
    made = client.post("/api/intake/FAX-0002/confirm", headers=DOLORES,
                       json={"values": values | {"side": "right"}, "checked": checked}).json()
    assert svc.cases.get(made["case_id"]).side == "right"


@pytest.mark.skipif(shutil.which("tesseract") is None, reason="Tesseract OCR is not installed")
def test_a_service_without_a_side_ignores_the_side_field(inbox):  # noqa: F811
    svc, client = inbox
    svc.workflow.catalog["services"]["27447"]["needs_side"] = True
    item = client.get("/api/intake/FAX-0001", headers=DOLORES).json()  # a CPAP request
    values = form_start(client, "FAX-0001")
    made = client.post("/api/intake/FAX-0001/confirm", headers=DOLORES,
                       json={"values": values, "checked": [f["key"] for f in item["form"] if f["problem"]]}).json()
    assert svc.cases.get(made["case_id"]).side is None
