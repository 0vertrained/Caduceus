"""The public demo: synthetic data in memory, development sign-in, and resets back to a clean queue."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from caduceus.api import create_app
from caduceus.app_factory import InsecureConfiguration, Settings, build_services
from caduceus.ratelimit import DemoLimits, Window
from caduceus.seed import seed
from tests.conftest import DATA
from tests.test_workflow_api import DOLORES, T0, Clock


def demo_settings(**kw) -> Settings:
    return Settings(data_dir=DATA, db_path=":memory:", auth_mode="demo", **kw)


@pytest.fixture()
def demo():
    """A demo app whose resets build fresh data on the same test clock."""
    clock = Clock(T0)

    def fresh():
        svc = build_services(demo_settings())
        svc.workflow.now = clock
        seed(svc, now=clock())
        return svc

    client = TestClient(create_app(fresh(), dispatch_interval=None, fresh_demo=fresh))
    return client, clock


def test_demo_refuses_a_model_api_key():
    with pytest.raises(InsecureConfiguration, match="anyone could spend it"):
        demo_settings(external_api_key="sk-test").check()


def test_demo_uses_the_development_roster_and_says_it_is_a_demo(demo):
    client, _ = demo
    assert client.get("/api/me", headers=DOLORES).json()["name"] == "Dolores Montoya"
    info = client.get("/api/demo").json()
    assert info["demo"] is True and info["reset_every_hours"] == 6


def test_reset_brings_back_a_clean_queue_but_not_more_than_once_a_minute(demo):
    client, clock = demo
    client.post("/api/cases/2026-1041/approve", headers=DOLORES)
    assert client.get("/api/cases/2026-1041", headers=DOLORES).json()["state"] == "submitted"

    assert client.post("/api/demo/reset", headers=DOLORES).status_code == 429  # just started: cooling down
    clock.advance(minutes=2)
    assert client.post("/api/demo/reset", headers=DOLORES).status_code == 200
    assert client.get("/api/cases/2026-1041", headers=DOLORES).json()["state"] == "review"
    assert client.post("/api/demo/reset", headers=DOLORES).status_code == 429


def test_reset_needs_a_signed_in_user(demo):
    client, clock = demo
    clock.advance(minutes=2)
    assert client.post("/api/demo/reset").status_code == 401


def test_outside_the_demo_there_is_no_banner_and_no_reset():
    svc = build_services(Settings(data_dir=DATA, db_path=":memory:", auth_mode="dev"))
    client = TestClient(create_app(svc, dispatch_interval=None))
    assert client.get("/api/demo").json() == {"demo": False}
    assert client.post("/api/demo/reset", headers=DOLORES).status_code == 404


def test_the_demo_starts_with_its_own_seeded_data(monkeypatch):
    monkeypatch.setenv("CADUCEUS_AUTH_MODE", "demo")
    monkeypatch.setenv("CADUCEUS_DATA_DIR", str(DATA))
    monkeypatch.setenv("CADUCEUS_DB", "/nonexistent/should-not-be-used.sqlite3")
    client = TestClient(create_app(dispatch_interval=None))
    rows = [r for g in client.get("/api/queue", headers=DOLORES).json()["groups"] for r in g["cases"]]
    assert len(rows) == 8  # three more referrals wait in the fax inbox
    assert [f["id"] for f in client.get("/api/intake", headers=DOLORES).json()["faxes"]] == ["FAX-0001", "FAX-0002",
                                                                                             "FAX-0003"]


# Behind Netlify ------------------------------------------------------------------------------------------------

KEY = "netlify-proxy-signing-key-for-tests-only-0123456789"


def netlify_signature(key: str = KEY, **claims) -> dict:
    import time

    import jwt
    body = {"iss": "netlify", "exp": int(time.time()) + 60, "deploy_context": "production",
            "site_url": "https://caduceus-demo.netlify.app"} | claims
    return {"x-nf-sign": jwt.encode(body, key, algorithm="HS256")}


@pytest.fixture()
def behind_netlify():
    svc = build_services(demo_settings(proxy_signing_key=KEY))
    seed(svc)
    return TestClient(create_app(svc, dispatch_interval=None))


def test_requests_netlify_signed_are_answered(behind_netlify):
    r = behind_netlify.get("/api/me", headers=DOLORES | netlify_signature())
    assert r.status_code == 200 and r.json()["name"] == "Dolores Montoya"


@pytest.mark.parametrize("headers", [
    {},                                                    # straight to the Cloud Run address
    netlify_signature(key="someone-elses-key-that-is-also-long-enough-0123"),
    netlify_signature(exp=1),                              # long expired
    netlify_signature(iss="not-netlify"),
    {"x-nf-sign": "not-a-token"},
])
def test_anything_else_is_refused(behind_netlify, headers):
    r = behind_netlify.get("/api/queue", headers=DOLORES | headers)
    assert r.status_code == 403
    assert "only through the Caduceus demo site" in r.json()["message"]
    assert r.headers["Cache-Control"] == "no-store" and "default-src 'self'" in r.headers["Content-Security-Policy"]


def test_health_answers_without_a_signature(behind_netlify):
    assert behind_netlify.get("/api/health").status_code == 200


def test_a_short_signing_key_is_refused():
    with pytest.raises(InsecureConfiguration, match="at least 32 bytes"):
        demo_settings(proxy_signing_key="short").check()


# Request limits ------------------------------------------------------------------------------------------------

def tight_limits(clock) -> DemoLimits:
    return DemoLimits(per_visitor=Window(5, 60, clock), writes_per_visitor=Window(2, 60, clock),
                      everyone=Window(8, 60, clock))


def test_a_visitor_is_slowed_down_and_then_let_back_in(demo):
    client, _ = demo
    t = [0.0]
    client.app.state.demo_limits = tight_limits(lambda: t[0])
    ip = {"x-forwarded-for": "203.0.113.7"}
    assert [client.get("/api/me", headers=DOLORES | ip).status_code for _ in range(6)] == [200] * 5 + [429]
    r = client.get("/api/me", headers=DOLORES | ip)
    assert r.status_code == 429 and int(r.headers["Retry-After"]) == 60 and "Too many requests" in r.json()["message"]
    t[0] = 61
    assert client.get("/api/me", headers=DOLORES | ip).status_code == 200


def test_writes_have_a_tighter_limit_and_visitors_are_counted_apart(demo):
    client, _ = demo
    client.app.state.demo_limits = tight_limits(lambda: 0.0)
    a, b = {"x-forwarded-for": "203.0.113.7"}, {"x-forwarded-for": "198.51.100.9"}
    writes = [client.post("/api/cases/2026-1041/undo", headers=DOLORES | a).status_code for _ in range(3)]
    assert writes[:2] != [429, 429] and writes[2] == 429
    assert client.get("/api/me", headers=DOLORES | b).status_code == 200


def test_a_ceiling_holds_however_many_addresses_a_caller_uses(demo):
    client, _ = demo
    client.app.state.demo_limits = tight_limits(lambda: 0.0)
    codes = [client.get("/api/me", headers=DOLORES | {"x-forwarded-for": f"192.0.2.{i}"}).status_code
             for i in range(10)]
    assert codes == [200] * 8 + [429] * 2


def test_health_is_never_limited(demo):
    client, _ = demo
    client.app.state.demo_limits = tight_limits(lambda: 0.0)
    assert all(client.get("/api/health").status_code == 200 for _ in range(20))


# Each visitor's own copy --------------------------------------------------------------------------------------

A, B = {"X-Caduceus-Sandbox": "a" * 32}, {"X-Caduceus-Sandbox": "b" * 32}


def state(client, case_id: str, sandbox: dict) -> str:
    return client.get(f"/api/cases/{case_id}", headers=DOLORES | sandbox).json()["state"]


def test_visitors_get_their_own_copy_of_the_cases(demo):
    client, _ = demo
    client.post("/api/cases/2026-1041/approve", headers=DOLORES | A)
    assert state(client, "2026-1041", A) == "submitted"
    assert state(client, "2026-1041", B) == "review" and state(client, "2026-1041", {}) == "review"
    bad = client.get("/api/me", headers=DOLORES | {"X-Caduceus-Sandbox": "../../etc"})
    assert bad.status_code == 400


def test_starting_over_resets_only_your_own_copy(demo):
    client, clock = demo
    for sandbox in (A, B):
        client.post("/api/cases/2026-1041/approve", headers=DOLORES | sandbox)
    clock.advance(minutes=2)
    assert client.post("/api/demo/reset", headers=DOLORES | A).status_code == 200
    assert state(client, "2026-1041", A) == "review" and state(client, "2026-1041", B) == "approved"


def test_a_later_visitor_sees_a_queue_that_just_arrived(demo):
    client, clock = demo
    first = client.get("/api/queue", headers=DOLORES | A).json()["groups"][0]["cases"][0]["due"]
    clock.advance(hours=3)
    later = client.get("/api/queue", headers=DOLORES | B).json()["groups"][0]["cases"][0]["due"]
    assert later["words"] == first["words"] == "In 1 hour 30 minutes"


def test_copies_expire_and_their_number_is_capped(demo, monkeypatch):
    import caduceus.api as api
    client, clock = demo
    live = client.app.state.live
    monkeypatch.setattr(api, "MAX_SANDBOXES", 2)
    for sid in ("1" * 32, "2" * 32, "3" * 32):
        client.get("/api/me", headers=DOLORES | {"X-Caduceus-Sandbox": sid})
    assert len(live.all()) == 3  # the shared set and the two most recent copies
    clock.advance(hours=7)
    live.expire()
    assert len(live.all()) == 1


def test_new_copies_per_address_are_limited(demo):
    client, _ = demo
    client.app.state.demo_limits.new_sandboxes = Window(2, 300, lambda: 0.0)
    codes = [client.get("/api/me", headers=DOLORES | {"X-Caduceus-Sandbox": f"{i:032x}"}).status_code
             for i in range(3)]
    assert codes == [200, 200, 429]
    assert client.get("/api/me", headers=DOLORES | {"X-Caduceus-Sandbox": f"{0:032x}"}).status_code == 200


# The guided tour ----------------------------------------------------------------------------------------------

def test_the_tour_follows_the_headline_case_to_the_payers_approval(demo):
    client, clock = demo
    reyes = {"X-Caduceus-User": "dr.reyes"} | A

    def now() -> dict:
        return client.get("/api/demo", headers=A).json()["tour"]

    def dispatch():
        clock.advance(seconds=31)
        client.get("/api/cases/2026-1043", headers=DOLORES | A)  # reading a case sends what is due

    t = now()
    assert (t["step"], t["actor"], t["path"]) == (1, "dolores", "/cases/2026-1043") and "Kristina Mante" in t["text"]
    client.post("/api/cases/2026-1043/request-attestation", headers=DOLORES | A)
    assert (now()["step"], now()["actor_name"]) == (2, "Dr. Ana Reyes")
    client.post("/api/attestations/2026-1043", headers=reyes, json={"answers": {"red_flags_excluded": "yes"}})
    assert now()["step"] == 3
    client.post("/api/cases/2026-1043/approve", headers=DOLORES | A)
    assert now()["step"] == 4 and now()["waiting"]
    dispatch()
    assert now()["step"] == 5
    client.post("/api/cases/2026-1043/request-note", headers=DOLORES | A)
    assert (now()["step"], now()["actor"]) == (6, "dr.reyes")
    client.post("/api/attestations/2026-1043/note", headers=reyes, json={"text": "Red flags ruled out on exam."})
    assert now()["step"] == 7
    client.post("/api/cases/2026-1043/send-note", headers=DOLORES | A)
    assert now()["step"] == 8 and now()["waiting"]
    dispatch()
    assert now()["done"] and now()["step"] == now()["steps"]
    assert client.get("/api/demo", headers=B).json()["tour"]["step"] == 1  # another visitor starts at the start
