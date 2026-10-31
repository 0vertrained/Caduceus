"""OIDC against a provider: the API takes the test identity provider's tokens and refuses the wrong ones, and the
provider checks what a real one checks of a public client, which the end-to-end walk (scripts/e2e-oidc.sh) relies on.

The provider is tests/mock_idp.py, on a real socket, because the API fetches its signing keys over HTTP."""

from __future__ import annotations

import os
import re
import secrets
import subprocess
import sys
from urllib.parse import parse_qsl, urlsplit

import httpx
import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi.testclient import TestClient

from caduceus.api import create_app
from caduceus.app_factory import Settings, build_services
from tests.conftest import DATA, ROOT, TEST_KEY
from tests.mock_idp import Client, MockIdP, s256

APP = "http://127.0.0.1:8766"
CALLBACK, SIGNED_OUT = f"{APP}/auth/callback", f"{APP}/signed-out"


@pytest.fixture(scope="module")
def idp():
    provider = MockIdP(Client("caduceus-ui", (CALLBACK,), (SIGNED_OUT,)), "caduceus-api").start()
    yield provider
    provider.stop()


@pytest.fixture()
def api(idp):
    svc = build_services(Settings(
        data_dir=DATA, db_path=":memory:", auth_mode="oidc", deid_key=TEST_KEY.decode(), oidc_issuer=idp.issuer,
        oidc_jwks_url=f"{idp.issuer}/protocol/openid-connect/certs", oidc_audience="caduceus-api",
        oidc_client_id="caduceus-ui", oidc_scope="openid profile"))
    return TestClient(create_app(svc, dispatch_interval=None))


@pytest.fixture()
def browser():
    """A cookie jar and no automatic redirects: each step of the flow is looked at."""
    with httpx.Client(follow_redirects=False) as http:
        yield http


def bearer(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


def authorize(idp: MockIdP, http: httpx.Client, verifier: str, **params: str) -> httpx.Response:
    query = {"client_id": "caduceus-ui", "redirect_uri": CALLBACK, "response_type": "code",
             "response_mode": "fragment", "scope": "openid profile", "state": "state-1", "nonce": "nonce-1",
             "code_challenge": s256(verifier), "code_challenge_method": "S256"} | params
    return http.get(f"{idp.issuer}/protocol/openid-connect/auth", params={k: v for k, v in query.items() if v})


def returned(response: httpx.Response) -> dict[str, str]:
    """What the provider sent back to the redirect URI, from the fragment (response_mode=fragment) or the query."""
    assert response.status_code == 302, response.text
    to = urlsplit(response.headers["location"])
    assert f"{to.scheme}://{to.netloc}{to.path}" in (CALLBACK, SIGNED_OUT)
    return dict(parse_qsl(to.fragment or to.query))


def choose(idp: MockIdP, http: httpx.Client, page: httpx.Response, persona: str) -> dict[str, str]:
    request_id = re.search(r'name="request" value="([^"]+)"', page.text).group(1)
    return returned(http.post(f"{idp.issuer}/protocol/openid-connect/auth",
                              data={"request": request_id, "persona": persona}))


def exchange(idp: MockIdP, http: httpx.Client, **form: str) -> httpx.Response:
    return http.post(f"{idp.issuer}/protocol/openid-connect/token",
                     data={"client_id": "caduceus-ui", "redirect_uri": CALLBACK} | form)


def sign_in(idp: MockIdP, http: httpx.Client, persona: str = "dolores") -> dict:
    """Authorization Code with PKCE, as the browser makes it: authorize, choose a person, exchange the code."""
    verifier = secrets.token_urlsafe(48)
    back = choose(idp, http, authorize(idp, http, verifier), persona)
    assert back["state"] == "state-1"
    r = exchange(idp, http, grant_type="authorization_code", code=back["code"], code_verifier=verifier)
    assert r.status_code == 200, r.text
    return r.json()


def refresh(idp: MockIdP, http: httpx.Client, token: str) -> httpx.Response:
    return exchange(idp, http, grant_type="refresh_token", refresh_token=token)


# The API ------------------------------------------------------------------------------------------------------------


def test_the_api_takes_who_and_what_role_from_the_providers_token(api, idp, browser):
    tokens = sign_in(idp, browser)
    assert api.get("/api/me", headers=bearer(tokens["access_token"])).json() == {
        "id": "dolores", "name": "Dolores Montoya", "role": "pa_specialist"}
    assert api.get("/api/queue", headers=bearer(tokens["access_token"])).status_code == 200
    renamed = idp.access_token("dolores", claims={"name": "D. Montoya"})  # the name is the token's, not the roster's
    assert api.get("/api/me", headers=bearer(renamed)).json()["name"] == "D. Montoya"


def test_a_clinician_is_matched_to_their_requests_by_the_clinician_id_claim(api, idp, browser):
    token = sign_in(idp, browser, "dr.reyes")["access_token"]
    assert api.get("/api/attestations", headers=bearer(token)).status_code == 200
    assert api.get("/api/queue", headers=bearer(token)).status_code == 403


def test_development_sign_in_is_off(api, idp):
    assert api.get("/api/me", headers={"X-Caduceus-User": "dolores"}).status_code == 401


ANOTHER_KEY = rsa.generate_private_key(public_exponent=65537, key_size=2048)
REFUSED = {
    "for another audience": {"audience": "another-api"},
    "from another issuer": {"issuer": "https://login.example.org/realms/mesaverde"},
    "expired": {"expires_in": -60},
    "with no expiry": {"drop": ["exp"]},
    "with no role": {"drop": ["caduceus_role"]},
    "with a role Caduceus does not have": {"claims": {"caduceus_role": "superuser"}},
    "for the agent's service identity": {"claims": {"caduceus_role": "agent_runtime"}},
    "with no subject": {"drop": ["sub"]},
    "with an empty subject": {"claims": {"sub": ""}},
    "signed with a key the provider never published": {"key": ANOTHER_KEY},
    "signed with a shared secret": {"key": "a-shared-secret-that-is-long-enough-for-hs256", "algorithm": "HS256"},
}


@pytest.mark.parametrize("change", REFUSED.values(), ids=REFUSED.keys())
def test_the_api_refuses_a_token(api, idp, change):
    assert api.get("/api/me", headers=bearer(idp.access_token("dolores"))).status_code == 200  # the one unchanged
    r = api.get("/api/me", headers=bearer(idp.access_token("dolores", **change)))
    assert r.status_code == 401 and r.json() == {"detail": "Sign in to continue."}


def test_the_api_refuses_the_ui_its_own_id_token(api, idp, browser):
    """Its audience is the UI's client, never the API (Settings.check makes sure the two differ)."""
    tokens = sign_in(idp, browser)
    assert api.get("/api/me", headers=bearer(tokens["id_token"])).status_code == 401


# The provider: what the end-to-end walk relies on --------------------------------------------------------------------


def test_the_provider_returns_the_code_in_the_fragment_and_names_who_signed_in(idp, browser):
    tokens = sign_in(idp, browser, "dr.reyes")
    claims = jwt.decode(tokens["access_token"], idp.key.public_key(), algorithms=["RS256"], audience="caduceus-api")
    assert {k: claims[k] for k in ("iss", "sub", "name", "caduceus_role", "clinician_id")} == {
        "iss": idp.issuer, "sub": "dr.reyes", "name": "Dr. Ana Reyes", "caduceus_role": "clinician",
        "clinician_id": "pract-reyes"}
    id_token = jwt.decode(tokens["id_token"], idp.key.public_key(), algorithms=["RS256"], audience="caduceus-ui")
    assert id_token["nonce"] == "nonce-1" and id_token["azp"] == "caduceus-ui"
    assert tokens["refresh_token"] and tokens["expires_in"] == idp.access_seconds


def test_the_provider_checks_the_pkce_verifier_and_a_code_is_spent_by_any_attempt(idp, browser):
    verifier = secrets.token_urlsafe(48)
    code = choose(idp, browser, authorize(idp, browser, verifier), "dolores")["code"]
    wrong = exchange(idp, browser, grant_type="authorization_code", code=code, code_verifier=secrets.token_urlsafe(48))
    assert wrong.status_code == 400 and wrong.json()["error"] == "invalid_grant"
    again = exchange(idp, browser, grant_type="authorization_code", code=code, code_verifier=verifier)
    assert again.status_code == 400 and again.json()["error"] == "invalid_grant"


def test_the_provider_checks_the_redirect_uri_at_both_ends(idp, browser):
    verifier = secrets.token_urlsafe(48)
    elsewhere = authorize(idp, browser, verifier, redirect_uri=f"{APP}/auth/callback/../../steal")
    assert elsewhere.status_code == 400 and "location" not in elsewhere.headers  # told to the person, sent nowhere
    code = choose(idp, browser, authorize(idp, browser, verifier), "dolores")["code"]
    r = exchange(idp, browser, grant_type="authorization_code", code=code, code_verifier=verifier,
                 redirect_uri=f"{APP}/other")
    assert r.status_code == 400 and r.json()["error"] == "invalid_grant"


@pytest.mark.parametrize("change", [{"code_challenge_method": "plain"}, {"code_challenge_method": ""},
                                    {"code_challenge": ""}, {"state": ""}, {"nonce": ""}, {"scope": "profile"},
                                    {"response_type": "token"}])
def test_the_provider_wants_s256_pkce_state_and_a_nonce(idp, browser, change):
    back = returned(authorize(idp, browser, secrets.token_urlsafe(48), **change))
    assert back["error"] in ("invalid_request", "invalid_scope", "unsupported_response_type") and "code" not in back


def test_a_code_used_twice_takes_back_what_it_gave(idp, browser):
    verifier = secrets.token_urlsafe(48)
    code = choose(idp, browser, authorize(idp, browser, verifier), "dolores")["code"]
    first = exchange(idp, browser, grant_type="authorization_code", code=code, code_verifier=verifier).json()
    replay = exchange(idp, browser, grant_type="authorization_code", code=code, code_verifier=verifier)
    assert replay.json()["error"] == "invalid_grant"
    assert refresh(idp, browser, first["refresh_token"]).json()["error"] == "invalid_grant"


def test_refresh_tokens_rotate_and_an_old_one_used_again_ends_the_grant(idp, browser):
    tokens = sign_in(idp, browser)
    renewed = refresh(idp, browser, tokens["refresh_token"]).json()
    assert renewed["refresh_token"] != tokens["refresh_token"] and renewed["access_token"]
    first, again = (jwt.decode(t["id_token"], options={"verify_signature": False}) for t in (tokens, renewed))
    assert again["auth_time"] == first["auth_time"] and "nonce" not in again  # the same sign-in, renewed
    assert refresh(idp, browser, tokens["refresh_token"]).json()["error"] == "invalid_grant"  # the old one, again
    assert refresh(idp, browser, renewed["refresh_token"]).json()["error"] == "invalid_grant"  # so the grant ended


def test_signed_in_at_the_provider_the_next_sign_in_needs_no_choice(idp, browser):
    sign_in(idp, browser)
    again = authorize(idp, browser, secrets.token_urlsafe(48))
    assert "code" in returned(again)  # the provider's session: straight back with a code
    asked = authorize(idp, browser, secrets.token_urlsafe(48), prompt="login")
    assert asked.status_code == 200 and "Choose who to sign in as" in asked.text


def test_sign_out_asks_first_then_ends_the_session_and_its_refresh_tokens(idp, browser):
    tokens = sign_in(idp, browser)
    end = f"{idp.issuer}/protocol/openid-connect/logout"
    ask = browser.get(end, params={"client_id": "caduceus-ui", "post_logout_redirect_uri": SIGNED_OUT})
    assert ask.status_code == 200 and "Sign out of Mesa Verde?" in ask.text  # no ID token in the address: it asks
    assert refresh(idp, browser, tokens["refresh_token"]).status_code == 200  # asking ended nothing yet
    stray = browser.post(end, data={"client_id": "caduceus-ui", "post_logout_redirect_uri": f"{APP}/elsewhere"})
    assert stray.status_code == 400 and "location" not in stray.headers
    done = browser.post(end, data={"client_id": "caduceus-ui", "post_logout_redirect_uri": SIGNED_OUT})
    assert done.status_code == 302 and done.headers["location"] == SIGNED_OUT
    page = authorize(idp, browser, secrets.token_urlsafe(48))
    assert page.status_code == 200 and "Choose who to sign in as" in page.text  # no session left to sign in with


def test_sign_out_ends_the_refresh_tokens_of_that_session(idp, browser):
    tokens = sign_in(idp, browser)
    browser.post(f"{idp.issuer}/protocol/openid-connect/logout",
                 data={"client_id": "caduceus-ui", "post_logout_redirect_uri": SIGNED_OUT})
    assert refresh(idp, browser, tokens["refresh_token"]).json()["error"] == "invalid_grant"


def test_the_token_endpoint_answers_another_origin_only_for_the_clients(idp, browser):
    for origin, allowed in ((APP, True), ("http://127.0.0.1:9999", False)):
        r = browser.post(f"{idp.issuer}/protocol/openid-connect/token", headers={"Origin": origin},
                         data={"client_id": "caduceus-ui", "grant_type": "refresh_token", "refresh_token": "x"})
        assert r.json()["error"] == "invalid_grant"
        assert r.headers.get("access-control-allow-origin") == (origin if allowed else None)


def test_the_token_endpoint_refuses_another_client_and_any_secret(idp, browser):
    for form in ({"client_id": "another"}, {"client_secret": "x"}):
        r = browser.post(f"{idp.issuer}/protocol/openid-connect/token",
                         data={"client_id": "caduceus-ui", "grant_type": "refresh_token", "refresh_token": "x"} | form)
        assert r.status_code == 401 and r.json()["error"] == "invalid_client"


def test_nothing_in_the_app_can_load_the_test_provider():
    """It signs anyone in without a password: it is outside the package and the image, and only pytest may import it."""
    sources = [p.relative_to(ROOT) for p in (ROOT / "src").rglob("*.py") if "mock_idp" in p.read_text()]
    assert sources == []
    out = subprocess.run([sys.executable, "-c", "import tests.mock_idp"], cwd=ROOT,  # noqa: S603 - fixed code
                         capture_output=True, text=True, env=os.environ | {"PYTHONPATH": str(ROOT / "src")})
    assert out.returncode != 0 and "nothing else may import it" in out.stderr
