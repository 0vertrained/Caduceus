"""The policies page: each service's running version, where its words come from, who approved it, and the versions
it replaced. Anyone on the prior authorization team may read it; a clinician has no use for it."""

from __future__ import annotations

from fastapi.testclient import TestClient

from caduceus.api import create_app
from caduceus.app_factory import Settings, build_services
from tests.conftest import DATA

SAM = {"X-Caduceus-User": "sam"}
REYES = {"X-Caduceus-User": "dr.reyes"}


def test_the_policies_page_shows_each_service_its_source_and_the_versions_it_replaced():
    client = TestClient(create_app(build_services(Settings(data_dir=DATA, db_path=":memory:", auth_mode="dev")),
                                   dispatch_interval=None))
    assert client.get("/api/policies", headers=REYES).status_code == 403
    services = {s["code"]: s for s in client.get("/api/policies", headers=SAM).json()["services"]}
    knee = services["27447"]
    assert knee["running"]["key"] == "knee-arthroplasty@2026-11" and knee["running"]["intact"]
    assert knee["running"]["source"]["document"] == "LCD L36007 (R10)"
    assert knee["running"]["approved_by"].startswith("Teresa Ortiz")
    assert [e["key"] for e in knee["earlier"]] == ["dev-knee-arthroplasty@2026-09"]
    arthritis = knee["running"]["criteria"][0]
    assert {"system": "ICD-10", "code": "M17.11", "display": "Unilateral primary osteoarthritis, right knee"} in \
        arthritis["codes"]
    assert services["72148"]["running"]["source"]["kind"] == "development-paraphrase"
