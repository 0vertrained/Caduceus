"""Signing in: what the browser is told (/api/auth-config), the page's policy for reaching the identity provider, and
the settings OIDC sign-in needs before the service will start."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from caduceus.api import SECURITY_HEADERS, create_app
from caduceus.app_factory import InsecureConfiguration, Settings, build_services
from caduceus.ratelimit import DemoLimits, Window
from tests.conftest import DATA, TEST_KEY

ISSUER = "https://login.mesaverde.example/realms/mesaverde"
OIDC = {"auth_mode": "oidc", "deid_key": TEST_KEY.decode(), "oidc_issuer": ISSUER,
        "oidc_jwks_url": f"{ISSUER}/protocol/openid-connect/certs", "oidc_audience": "caduceus-api",
        "oidc_client_id": "caduceus-ui", "oidc_scope": "openid profile"}


def settings(**kw) -> Settings:
    return Settings(data_dir=DATA, db_path=":memory:", **kw)


def client(**kw) -> TestClient:
    return TestClient(create_app(build_services(settings(**kw)), dispatch_interval=None))


def directives(policy: str) -> dict[str, str]:
    return dict(d.strip().split(" ", 1) for d in policy.split(";"))


def test_development_and_the_demo_say_only_which_sign_in_they_use():
    assert client(auth_mode="dev").get("/api/auth-config").json() == {"mode": "dev"}
    assert client(auth_mode="demo").get("/api/auth-config").json() == {"mode": "demo"}


def test_asking_how_to_sign_in_does_not_count_toward_the_demos_limits():
    """Every page load asks it first: counting it would spend a visitor's allowance on nothing."""
    demo = client(auth_mode="demo")
    demo.app.state.demo_limits = DemoLimits(per_visitor=Window(5, 300))
    for _ in range(10):
        assert demo.get("/api/auth-config").status_code == 200
    for _ in range(5):
        assert demo.get("/api/demo").status_code == 200  # the whole allowance is still there
    assert demo.get("/api/demo").status_code == 429  # and it still holds for everything else


def test_with_oidc_the_browser_learns_what_a_public_client_needs_and_nothing_else():
    r = client(**OIDC).get("/api/auth-config")  # no sign-in needed to ask how to sign in
    assert r.status_code == 200 and r.headers["Cache-Control"] == "no-store"
    assert r.json() == {"mode": "oidc", "issuer": ISSUER, "client_id": "caduceus-ui", "scope": "openid profile"}


def test_with_oidc_the_page_may_connect_to_the_issuer_and_nowhere_wider():
    headers = client(**OIDC).get("/api/health").headers
    sent, base = directives(headers["Content-Security-Policy"]), directives(SECURITY_HEADERS["Content-Security-Policy"])
    assert sent.pop("connect-src") == f"'self' {ISSUER}/"  # the issuer and the addresses under it: not its whole host
    assert sent == base  # every other directive is the base policy's
    assert {k: headers[k] for k in SECURITY_HEADERS if k != "Content-Security-Policy"} == {
        k: v for k, v in SECURITY_HEADERS.items() if k != "Content-Security-Policy"}


def test_development_and_the_demo_keep_the_headers_netlify_sends():
    """The same headers as the demo site's (tests/test_netlify.py), which netlify.toml sends unchanged."""
    for mode in ("dev", "demo"):
        headers = client(auth_mode=mode).get("/api/health").headers
        assert {k: headers[k] for k in SECURITY_HEADERS} == SECURITY_HEADERS


def test_oidc_needs_the_ui_client_id_and_says_so():
    with pytest.raises(InsecureConfiguration, match="CADUCEUS_OIDC_CLIENT_ID"):
        settings(**OIDC | {"oidc_client_id": None}).check()
    with pytest.raises(InsecureConfiguration, match="CADUCEUS_OIDC_ISSUER, CADUCEUS_OIDC_JWKS_URL"):
        settings(**OIDC | {"oidc_issuer": None, "oidc_jwks_url": ""}).check()
    settings(**OIDC).check()


@pytest.mark.parametrize("client_id", ["caduceus ui", "caduceus-ui\n", "caduceus\"ui", "caduceus;ui", "x" * 129])
def test_a_client_id_that_is_not_a_plain_name_is_refused(client_id):
    with pytest.raises(InsecureConfiguration, match="CADUCEUS_OIDC_CLIENT_ID must be"):
        settings(**OIDC | {"oidc_client_id": client_id}).check()


def test_the_api_audience_must_not_be_the_ui_client():
    """Else the UI's ID token, whose audience is the client, would be taken as an access token for the API."""
    with pytest.raises(InsecureConfiguration, match="ID token"):
        settings(**OIDC | {"oidc_audience": "caduceus-ui"}).check()


@pytest.mark.parametrize("issuer", [
    "http://login.mesaverde.example/realms/mesaverde",       # not https, and not this machine
    "https://login.mesaverde.example/realms/mesaverde?x=1",  # a query
    "https://login.mesaverde.example/realms/mesaverde#x",    # a fragment
    "https://someone:secret@login.mesaverde.example/realms/mesaverde",
    "https://login.mesaverde.example/realms/x; script-src *",  # would add a directive to the page's policy
    "https://login.mesaverde.example/realms/x 'unsafe-inline'",
    "ftp://login.mesaverde.example/realms/mesaverde",
    "login.mesaverde.example",
    "https://login.mesaverde.example:443443/realms/mesaverde",  # not a port
    "http://[::1:8080/realms/mesaverde",
])
def test_an_issuer_that_is_not_a_plain_https_address_is_refused(issuer):
    with pytest.raises(InsecureConfiguration, match="CADUCEUS_OIDC_ISSUER must be"):
        settings(**OIDC | {"oidc_issuer": issuer}).check()


def test_an_issuer_may_use_http_only_on_this_machine():
    for issuer in ("http://localhost:8080/realms/mesaverde", "http://127.0.0.1:8080/realms/mesaverde",
                   "http://[::1]:8080/realms/mesaverde"):
        settings(**OIDC | {"oidc_issuer": issuer}).check()


def test_the_scope_must_ask_for_openid():
    for scope in ("profile email", "openid \"profile\"", "openid pro\\file", ""):
        with pytest.raises(InsecureConfiguration, match="CADUCEUS_OIDC_SCOPE"):
            settings(**OIDC | {"oidc_scope": scope}).check()
    settings(**OIDC | {"oidc_scope": "openid profile offline_access"}).check()


def test_the_deid_key_is_still_checked_first():
    """CI's image job starts the container with no settings and expects this refusal, before any about sign-in."""
    with pytest.raises(InsecureConfiguration, match="CADUCEUS_DEID_KEY"):
        settings(**OIDC | {"deid_key": None, "oidc_issuer": None, "oidc_client_id": None}).check()


def test_sign_in_settings_are_not_needed_outside_oidc():
    settings(auth_mode="dev", oidc_issuer="not even an address", oidc_client_id="a b").check()
