"""The local Keycloak (docker compose --profile oidc) and its realm, infra/keycloak/mesaverde-realm.json: what they
promise for sign-in, read from the files. Keycloak itself is not run here or in CI; docs/sign-in.md says so."""

from __future__ import annotations

import json
import re

import yaml

from caduceus.app_factory import Settings
from caduceus.auth import DEV_USERS, Role
from tests.conftest import DATA, ROOT, TEST_KEY

REALM_FILE = ROOT / "infra" / "keycloak" / "mesaverde-realm.json"
REALM = json.loads(REALM_FILE.read_text())
COMPOSE = yaml.safe_load((ROOT / "docker-compose.yml").read_text())
CLIENT = next(c for c in REALM["clients"] if c["clientId"] == "caduceus-ui")
API, KEYCLOAK = COMPOSE["services"]["api"], COMPOSE["services"]["keycloak"]
UI = {"http://127.0.0.1:8000", "http://localhost:8000"}  # where compose serves the UI (api's published port)


def default(value: str) -> str:
    """The default of a compose ${VARIABLE:-default}."""
    return re.fullmatch(r"\$\{\w+:-(.*)\}", value).group(1)


def mapper(name: str) -> dict:
    return next(m for m in CLIENT["protocolMappers"] if m["name"] == name)


def test_the_ui_is_a_public_client_with_the_code_flow_and_pkce_s256_only():
    assert CLIENT["publicClient"] is True and CLIENT["standardFlowEnabled"] is True
    assert not (CLIENT["implicitFlowEnabled"] or CLIENT["directAccessGrantsEnabled"]
                or CLIENT["serviceAccountsEnabled"])
    assert CLIENT["attributes"]["pkce.code.challenge.method"] == "S256"
    assert CLIENT["fullScopeAllowed"] is False and CLIENT["optionalClientScopes"] == []  # no offline_access


def test_its_addresses_are_exact_and_are_where_compose_serves_the_ui():
    assert API["ports"] == ["127.0.0.1:8000:8000"]
    assert set(CLIENT["redirectUris"]) == {f"{u}/auth/callback" for u in UI}
    assert set(CLIENT["attributes"]["post.logout.redirect.uris"].split("##")) == {f"{u}/signed-out" for u in UI}
    assert set(CLIENT["webOrigins"]) == UI  # the token endpoint answers the page, and no other site
    assert not any("*" in u for u in [*CLIENT["redirectUris"], *CLIENT["webOrigins"]])


def test_access_tokens_carry_the_apis_audience_the_role_and_the_clinician():
    audience = mapper("caduceus-api audience")
    assert audience["protocolMapper"] == "oidc-audience-mapper"
    assert audience["config"]["included.custom.audience"] == default(API["environment"]["CADUCEUS_OIDC_AUDIENCE"])
    assert audience["config"]["access.token.claim"] == "true"
    for claim in ("caduceus_role", "clinician_id"):
        config = mapper(claim)["config"]
        assert mapper(claim)["protocolMapper"] == "oidc-usermodel-attribute-mapper"
        assert config["user.attribute"] == config["claim.name"] == claim
        assert config["access.token.claim"] == "true" and config["multivalued"] == "false"
        assert config["jsonType.label"] == "String"  # the API reads one string, never a list
    assert {"basic", "profile"} <= set(CLIENT["defaultClientScopes"])  # sub, and name


def test_the_people_are_the_development_roster():
    users = {u["username"]: u for u in REALM["users"]}
    assert set(users) == set(DEV_USERS)
    for key, person in DEV_USERS.items():
        user = users[key]
        assert user["id"] == person.id  # their subject, which the case history names them by
        assert f"{user['firstName']} {user['lastName']}" == person.name  # Keycloak's name claim
        assert user["attributes"]["caduceus_role"] == [person.role.value]
        assert user["attributes"].get("clinician_id") == ([person.clinician_id] if person.clinician_id else None)


def test_only_an_administrator_sees_or_sets_a_role():
    """A person who could set their own caduceus_role could make themselves an administrator."""
    provider = REALM["components"]["org.keycloak.userprofile.UserProfileProvider"][0]
    profile = json.loads(provider["config"]["kc.user.profile.config"][0])
    attributes = {a["name"]: a for a in profile["attributes"]}
    for name in ("caduceus_role", "clinician_id"):
        assert attributes[name]["permissions"] == {"view": ["admin"], "edit": ["admin"]}
    assert attributes["caduceus_role"]["validations"]["options"]["options"] == [
        r.value for r in Role if r != Role.AGENT_RUNTIME]
    assert "unmanagedAttributePolicy" not in profile  # no attribute anyone adds for themselves
    assert REALM["registrationAllowed"] is False and REALM["editUsernameAllowed"] is False


def test_refresh_tokens_rotate_and_end_with_the_session():
    assert REALM["revokeRefreshToken"] is True and REALM["refreshTokenMaxReuse"] == 0
    assert REALM["accessTokenLifespan"] <= 300
    assert REALM["ssoSessionIdleTimeout"] <= 3600 and REALM["ssoSessionMaxLifespan"] <= 12 * 3600


def test_no_password_is_written_down():
    credentials = [c for u in REALM["users"] for c in u["credentials"]]
    assert len(credentials) == len(DEV_USERS)
    assert all(c == {"type": "password", "value": "${CADUCEUS_KEYCLOAK_USER_PASSWORD}", "temporary": False}
               for c in credentials)
    assert "hashedSaltedValue" not in REALM_FILE.read_text() and "secret" not in json.dumps(CLIENT).lower()
    env = KEYCLOAK["environment"]
    assert env["CADUCEUS_KEYCLOAK_USER_PASSWORD"] == "${CADUCEUS_KEYCLOAK_USER_PASSWORD:-}"
    assert env["KC_BOOTSTRAP_ADMIN_PASSWORD"] == "${CADUCEUS_KEYCLOAK_ADMIN_PASSWORD:-}"
    example = (ROOT / ".env.example").read_text().splitlines()
    assert {line for line in example if line.startswith("CADUCEUS_KEYCLOAK_")} == {
        "CADUCEUS_KEYCLOAK_ADMIN_PASSWORD=", "CADUCEUS_KEYCLOAK_USER_PASSWORD="}


def test_compose_points_the_api_at_keycloak_and_keeps_the_way_between_them_inside():
    env, hostname = API["environment"], KEYCLOAK["environment"]["KC_HOSTNAME"]
    issuer = default(env["CADUCEUS_OIDC_ISSUER"])
    assert issuer == f"{hostname}/realms/{REALM['realm']}"  # the address the browser uses, which every token names
    assert default(env["CADUCEUS_OIDC_JWKS_URL"]) == (
        f"http://keycloak:8080/realms/{REALM['realm']}/protocol/openid-connect/certs")
    assert default(env["CADUCEUS_OIDC_CLIENT_ID"]) == CLIENT["clientId"]
    Settings(data_dir=DATA, db_path=":memory:", auth_mode="oidc", deid_key=TEST_KEY.decode(), oidc_issuer=issuer,
             oidc_jwks_url=default(env["CADUCEUS_OIDC_JWKS_URL"]), oidc_scope="openid profile",
             oidc_audience=default(env["CADUCEUS_OIDC_AUDIENCE"]), oidc_client_id=CLIENT["clientId"]).check()
    shared = set(API["networks"]) & set(KEYCLOAK["networks"])
    assert shared and all(COMPOSE["networks"][n].get("internal") is True for n in shared)
    assert KEYCLOAK["ports"] == ["127.0.0.1:8080:8080"]  # the browser's way in, from this machine only
    assert KEYCLOAK["profiles"] == ["oidc"]
    assert re.fullmatch(r"quay\.io/keycloak/keycloak:\d+\.\d+\.\d+@sha256:[0-9a-f]{64}", KEYCLOAK["image"])
    assert KEYCLOAK["volumes"] == [
        "./infra/keycloak/mesaverde-realm.json:/opt/keycloak/data/import/mesaverde-realm.json:ro"]
