"""The administrator's page: what it shows, who may see it, and the external model's off switch, which holds across
a restart because the audit log records it."""

from __future__ import annotations

import sqlite3

from fastapi.testclient import TestClient

from caduceus.api import create_app
from caduceus.app_factory import Settings, build_services
from caduceus.seed import seed
from tests.conftest import DATA
from tests.test_workflow_api import DOLORES, T0

AVERY = {"X-Caduceus-User": "avery"}
SAM = {"X-Caduceus-User": "sam"}


def _app(conn=None, **kw):
    svc = build_services(Settings(data_dir=DATA, db_path=":memory:", auth_mode="dev", external_provider="stub",
                                  external_api_key="stub-key", **kw), conn=conn)
    svc.workflow.now = lambda: T0
    return svc, TestClient(create_app(svc, dispatch_interval=None))


def test_only_an_administrator_sees_the_administration_page():
    _, client = _app()
    assert client.get("/api/admin", headers=DOLORES).status_code == 403
    assert client.get("/api/admin", headers=SAM).status_code == 403
    page = client.get("/api/admin", headers=AVERY).json()
    assert page["routes"]["external_on"] is True and page["routes"]["external"]["model"]
    assert page["audit"]["intact"] is True and page["database"] == "SQLite"
    assert {p["title"] for p in page["policies"]} >= {"Total knee arthroplasty"}
    assert all(p["intact"] for p in page["policies"])
    assert "stub-key" not in str(page)  # names of models, never keys


def test_the_external_route_switches_off_drafts_fall_back_and_the_switch_survives_a_restart():
    conn = sqlite3.connect(":memory:", check_same_thread=False)
    svc, client = _app(conn=conn)
    seed(svc, now=T0)
    off = client.post("/api/admin/external-route", headers=AVERY, json={"on": False}).json()
    assert off["routes"]["external_on"] is False
    assert client.post("/api/admin/external-route", headers=DOLORES, json={"on": True}).status_code == 403
    case = svc.cases.all()[0]
    svc.workflow.prepare(case.id)  # drafts again: with the route off and no in-VPC model, the template writes it
    assert svc.cases.get(case.id).draft["route"] == "template"
    switched = [e for e in svc.audit.events() if e.action == "admin.external_route"]
    assert switched[-1].actor == "avery" and switched[-1].detail == {"on": False}

    again, _ = _app(conn=conn)  # a restart on the same database
    assert again.router.external_on is False
