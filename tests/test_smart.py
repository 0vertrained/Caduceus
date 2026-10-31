"""The SMART EHR launch, at the protocol: discovery, state, PKCE, the code exchange and the bounded read. A fake SMART
server (tests/fake_smart.py) stands in for the EHR through httpx.MockTransport, so nothing here leaves the process."""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from urllib.parse import parse_qs, urlsplit

import httpx
import pytest

from caduceus.app_factory import InsecureConfiguration, Settings
from caduceus.smart import (
    SANDBOX_ISS,
    WORDS,
    Budget,
    LaunchRefused,
    Limits,
    SmartLaunch,
    challenge,
    granted,
    inside,
    lookback,
    safe_url,
    scopes,
)
from tests.conftest import DATA
from tests.fake_smart import PATIENT_ID, PRACTITIONER, FakeSmart, patient, record

REDIRECT = "https://caduceus.example/smart/callback"
NOW = datetime(2026, 10, 29, 19, 0, tzinfo=UTC)
TODAY = NOW.date()
WINDOWS = {"Condition": None, "Procedure": 730, "Observation": 365}


def smart(fake: FakeSmart, **limits) -> SmartLaunch:
    return SmartLaunch([fake.base], [REDIRECT], fake.client_id, transport=fake.transport(), limits=Limits(**limits))


def begin(s: SmartLaunch, fake: FakeSmart, sandbox: str | None = "a" * 32, user: str = "dolores"):
    return s.begin(fake.base, "launch-ctx-1", REDIRECT, sandbox, user, NOW)


def launched(s: SmartLaunch, fake: FakeSmart, **kw):
    pending, url = begin(s, fake)
    code, state = fake.authorize(url)
    return s.finish(code, state, "a" * 32, "dolores", kw.get("now", NOW), TODAY, WINDOWS)


@pytest.fixture()
def fake():
    return FakeSmart(as_of=TODAY)


# Starting -------------------------------------------------------------------------------------------------------

def test_discovery_gives_an_authorize_url_with_pkce_and_the_least_scope(fake):
    s = smart(fake)
    pending, url = begin(s, fake)
    u = urlsplit(url)
    q = {k: v[0] for k, v in parse_qs(u.query).items()}
    assert f"{u.scheme}://{u.netloc}{u.path}" == fake.auth_base + "/authorize"
    assert q["response_type"] == "code" and q["client_id"] == "caduceus-demo" and q["redirect_uri"] == REDIRECT
    assert q["launch"] == "launch-ctx-1" and q["aud"] == fake.base and q["state"] == pending.state
    assert q["code_challenge_method"] == "S256" and q["code_challenge"] == challenge(pending.verifier)
    assert len(pending.state) == 43 and 43 <= len(pending.verifier) <= 128
    assert q["scope"] == ("launch openid fhirUser patient/Patient.r patient/Condition.rs patient/Procedure.rs "
                          "patient/Observation.rs patient/DocumentReference.rs")
    assert [r.url.path for r in fake.requests] == ["/v/r4/fhir/.well-known/smart-configuration"]
    assert pending.verifier not in repr(pending)  # never printed, not even by accident


def test_a_server_without_smart_v2_is_asked_in_v1_scopes(fake):
    fake.v2 = False
    _, url = begin(smart(fake), fake)
    assert parse_qs(urlsplit(url).query)["scope"][0] == scopes(v2=False)
    assert "patient/Observation.read" in scopes(v2=False) and ".rs" not in scopes(v2=False)


def test_an_iss_off_the_allowlist_is_refused_before_anything_is_fetched(fake):
    s = smart(fake)
    for iss in ("https://evil.example/v/r4/fhir", fake.base + "/../../evil", "http://ehr.example/v/r4/fhir",
                fake.base.upper()):
        with pytest.raises(LaunchRefused) as e:
            s.begin(iss, "x", REDIRECT, None, "dolores", NOW)
        assert e.value.reason == "iss_not_allowed" and e.value.status == 400
    assert fake.requests == []
    s.begin(fake.base + "/", "x", REDIRECT, None, "dolores", NOW)  # the same base with a trailing slash is the same


def test_a_redirect_uri_or_launch_it_was_not_set_up_for_is_refused(fake):
    s = smart(fake)
    with pytest.raises(LaunchRefused, match="not set up for this address"):
        s.begin(fake.base, "x", "https://evil.example/smart/callback", None, "dolores", NOW)
    with pytest.raises(LaunchRefused) as e:
        s.begin(fake.base, "has spaces in it", REDIRECT, None, "dolores", NOW)
    assert e.value.reason == "bad_launch" and fake.requests == []


@pytest.mark.parametrize("config, reason", [
    ({"authorization_endpoint": "https://evil.example/authorize", "token_endpoint": "https://ehr.example/token"},
     "discovery_invalid"),
    ({"authorization_endpoint": "https://ehr.example/authorize", "token_endpoint": "https://ehr.example:8443/t"},
     "discovery_invalid"),
    ({"authorization_endpoint": "https://ehr.example/authorize"}, "discovery_invalid"),
    ({"authorization_endpoint": "https://ehr.example/a", "token_endpoint": "https://ehr.example/t",
      "code_challenge_methods_supported": ["plain"]}, "no_pkce"),
])
def test_a_configuration_that_points_elsewhere_or_lacks_pkce_is_refused(fake, config, reason):
    fake.discovery = config
    with pytest.raises(LaunchRefused) as e:
        begin(smart(fake), fake)
    assert e.value.reason == reason


def test_an_ehr_that_is_down_or_slow_is_said_in_plain_words(fake):
    fake.fail = {"/smart-configuration": httpx.ConnectError("refused")}
    with pytest.raises(LaunchRefused, match="could not be reached"):
        begin(smart(fake), fake)
    fake.fail = {"/smart-configuration": httpx.ReadTimeout("slow")}
    with pytest.raises(LaunchRefused) as e:
        begin(smart(fake), fake)
    assert e.value.reason == "timeout" and e.value.status == 504


# Finishing ------------------------------------------------------------------------------------------------------

def test_a_launch_reads_the_patient_within_the_criteria_windows(fake):
    read = launched(smart(fake), fake)
    assert read.patient["id"] == PATIENT_ID and read.patient["name"][0]["family"] == "Mares"
    assert "text" not in read.patient and "meta" not in read.patient and "maritalStatus" not in read.patient
    kinds = [r["resourceType"] for r in read.resources]
    assert read.counts == {"Condition": 6, "Procedure": 4, "Observation": 24, "DocumentReference": 8}
    assert kinds.count("Procedure") == 4  # physical therapy 1,300 days ago is outside the two-year window
    assert [n["description"] for n in read.notes] == ["Progress note", "Progress note", "History and physical note",
                                                      "Progress note", "Progress note"]  # the PDF has no text
    assert read.audit["ehr_user"] == PRACTITIONER and read.audit["requests"] == 6 and read.audit["dropped"] == 0
    searched = {r.url.path.rsplit("/", 1)[-1]: dict(r.url.params) for r in fake.requests if "patient" in r.url.params}
    assert searched["Observation"]["date"] == f"ge{TODAY - timedelta(days=365)}"
    assert searched["Procedure"]["date"] == f"ge{TODAY - timedelta(days=730)}"
    assert "date" not in searched["Condition"] and searched["DocumentReference"]["_sort"] == "-date"


def test_a_state_is_used_once(fake):
    s = smart(fake)
    pending, url = begin(s, fake)
    code, state = fake.authorize(url)
    s.finish(code, state, "a" * 32, "dolores", NOW, TODAY, WINDOWS)
    with pytest.raises(LaunchRefused) as e:
        s.finish(code, state, "a" * 32, "dolores", NOW, TODAY, WINDOWS)
    assert e.value.reason == "state_unknown" and fake.hits("/token") == 1 and s.pending() == 0


def test_an_expired_state_is_refused_without_asking_the_ehr(fake):
    s = smart(fake)
    _, url = begin(s, fake)
    code, state = fake.authorize(url)
    with pytest.raises(LaunchRefused) as e:
        s.finish(code, state, "a" * 32, "dolores", NOW + timedelta(minutes=11), TODAY, WINDOWS)
    assert e.value.reason == "state_expired" and fake.hits("/token") == 0
    with pytest.raises(LaunchRefused, match="already been used"):  # and it is spent
        s.finish(code, state, "a" * 32, "dolores", NOW, TODAY, WINDOWS)


@pytest.mark.parametrize("sandbox, user", [("b" * 32, "dolores"), ("a" * 32, "avery"), (None, "dolores")])
def test_a_state_finished_by_someone_else_or_in_another_copy_is_refused(fake, sandbox, user):
    s = smart(fake)
    _, url = begin(s, fake)
    code, state = fake.authorize(url)
    with pytest.raises(LaunchRefused) as e:
        s.finish(code, state, sandbox, user, NOW, TODAY, WINDOWS)
    assert e.value.reason == "state_unknown" and fake.hits("/token") == 0


def test_the_ehr_exchanges_the_code_only_for_the_verifier_behind_its_challenge(fake):
    s = smart(fake)
    pending, url = begin(s, fake)
    code, state = fake.authorize(url)
    pending.verifier = "x" * 86  # not the one the challenge was made from
    with pytest.raises(LaunchRefused) as e:
        s.finish(code, state, "a" * 32, "dolores", NOW, TODAY, WINDOWS)
    assert e.value.reason == "token_refused" and e.value.detail == {"status": 401, "oauth_error": "invalid_grant"}
    sent = parse_qs(next(r for r in fake.requests if r.url.path.endswith("/token")).content.decode())
    assert sent["grant_type"] == ["authorization_code"] and sent["code_verifier"] == ["x" * 86]
    assert sent["redirect_uri"] == [REDIRECT] and sent["client_id"] == ["caduceus-demo"]


def test_a_refused_exchange_keeps_the_ehrs_own_words_off_the_screen(fake):
    fake.token_status = 400
    with pytest.raises(LaunchRefused) as e:
        launched(smart(fake), fake)
    assert e.value.message == WORDS["token_refused"] and "trace" not in str(e.value.audit)


def test_pages_are_followed_while_they_stay_on_the_fhir_server(fake):
    fake.page_size = 4
    read = launched(smart(fake), fake)
    # Each later page is fetched from the base itself (?_getpages=...): 5 more of observations, 1 of diagnoses.
    assert read.counts["Observation"] == 24 and fake.hits("/fhir") == 6
    assert all(r.headers["authorization"].startswith("Bearer ") for r in fake.requests if "/fhir" in r.url.path
               and "smart-configuration" not in r.url.path)


def test_a_next_link_off_the_fhir_server_is_never_followed(fake):
    fake.page_size, fake.next_link_base = 4, "https://evil.example/v/r4/fhir"
    with pytest.raises(LaunchRefused) as e:
        launched(smart(fake), fake)
    assert e.value.reason == "bad_response" and e.value.detail == {"step": "Condition paging"}
    assert not [r for r in fake.requests if r.url.host == "evil.example"]  # the token went nowhere else


@pytest.mark.parametrize("limits, reached", [({"resources": 20}, "resources"), ({"bytes": 6_000}, "bytes"),
                                             ({"pages": 2}, "pages")])
def test_a_record_over_a_limit_is_refused_whole(fake, limits, reached):
    fake.page_size = 4
    with pytest.raises(LaunchRefused) as e:
        launched(smart(fake, **limits), fake)
    assert (e.value.reason, e.value.detail, e.value.status) == ("too_large", {"limit": reached}, 422)


def test_a_slow_answer_runs_out_of_time(fake):
    fake.fail = {"/Observation": httpx.ReadTimeout("slow")}
    with pytest.raises(LaunchRefused, match="took too long"):
        launched(smart(fake), fake)
    t = [0.0]
    budget = Budget(Limits(deadline=20, timeout=10), lambda: t[0])
    assert budget.left() == 10
    t[0] = 15
    assert budget.left() == 5  # the last request gets only what is left of the deadline
    t[0] = 21
    with pytest.raises(LaunchRefused, match="took too long"):
        budget.left()


def test_entries_about_anyone_else_are_dropped(fake):
    stranger = record(TODAY, pid="someone-else")[0]
    fake.resources = record(TODAY) + [stranger, {"resourceType": "Condition", "id": "no subject"}]
    read = launched(smart(fake), fake)
    assert read.counts["Condition"] == 6 and read.audit["dropped"] == 2


def test_a_patient_without_a_name_or_a_full_birth_date_is_refused(fake):
    fake.patient_resource = patient() | {"birthDate": "1957"}
    with pytest.raises(LaunchRefused, match="no full date of birth"):
        launched(smart(fake), fake)
    fake.patient_resource = patient() | {"name": [{"text": 42}]}
    with pytest.raises(LaunchRefused) as e:
        launched(smart(fake), fake)
    assert e.value.reason == "incomplete_patient"


def test_what_the_ehr_did_not_grant_is_not_asked_for(fake):
    fake.grant = "launch openid fhirUser patient/Patient.r patient/Condition.rs"
    read = launched(smart(fake), fake)
    assert read.not_shared == ["Procedure", "Observation", "DocumentReference"] and read.notes == []
    assert not [r for r in fake.requests if r.url.path.endswith(("/Procedure", "/Observation"))]
    fake.grant = "launch openid fhirUser patient/Condition.rs"
    with pytest.raises(LaunchRefused, match="did not share this patient"):
        launched(smart(fake), fake)


def test_an_id_token_for_another_app_is_ignored(fake):
    fake.id_token_audience = "another-app"  # its fhirUser is not taken; the launch itself goes on
    read = launched(smart(fake), fake)
    assert read.audit["ehr_user"] is None and read.counts["Condition"] == 6


# The pieces ------------------------------------------------------------------------------------------------------

def test_granted_scopes_in_either_syntax():
    assert granted("patient/Observation.rs", "Observation", search=True)
    assert granted("patient/*.read", "Observation", search=True) and granted("user/Patient.r", "Patient", False)
    assert granted("patient/Observation.rs?category=laboratory", "Observation", search=True)
    assert not granted("patient/Observation.r", "Observation", search=True)  # read but not search
    assert not granted("patient/Observation.write patient/Condition.rs", "Observation", search=True)
    assert not granted("system/*.read launch openid", "Observation", search=True)


def test_the_record_is_read_as_far_back_as_the_criteria_look_and_no_further(library):
    # The reviewed CPAP criteria accept a sleep test, and its index, from any date, so those types are read whole.
    assert lookback(library) == {"Condition": None, "Procedure": None, "Observation": None}
    lumbar = {k: s for k, s in library.items() if k.startswith("dev-lumbar-mri@")}
    assert lookback(lumbar) == {"Condition": None, "Procedure": 365, "Observation": 120}


def test_only_addresses_on_the_fhir_server_get_the_token():
    base = "https://ehr.example/v/r4/fhir"
    assert inside(base + "?_getpages=1", base) and inside(base + "/Observation?patient=1", base)
    for url in ("https://ehr.example/v/r4/fhirx", "https://ehr.example/v/r4", "http://ehr.example/v/r4/fhir/x",
                "https://evil.example/v/r4/fhir/x", "https://user@ehr.example/v/r4/fhir/x",
                "https://ehr.example:444/v/r4/fhir/x"):
        assert not inside(url, base), url
    assert safe_url("http://127.0.0.1:8766/v/r4/fhir") and not safe_url("http://ehr.example/fhir")
    assert not safe_url("https://ehr.example:notaport/fhir")


def test_the_demo_launches_from_the_smart_sandbox_and_nothing_else_does_unless_set(monkeypatch):
    for name in ("CADUCEUS_SMART_ISS", "CADUCEUS_SMART_REDIRECT_URIS"):
        monkeypatch.delenv(name, raising=False)
    assert Settings(data_dir=DATA, auth_mode="demo").smart_allowed == SANDBOX_ISS
    assert Settings(data_dir=DATA, auth_mode="dev").smart_allowed == ()
    assert Settings(data_dir=DATA, auth_mode="demo", smart_iss="").smart_allowed == ()
    s = Settings(data_dir=DATA, auth_mode="dev", smart_iss="https://a.example/fhir, https://b.example/fhir",
                 smart_redirect_uris="http://localhost:5173/smart/callback")
    assert s.smart_allowed == ("https://a.example/fhir", "https://b.example/fhir")
    s.check()


@pytest.mark.parametrize("kw", [{"smart_iss": "http://ehr.example/fhir"},
                                {"smart_redirect_uris": "http://caduceus.example/smart/callback"},
                                {"smart_iss": "https://ehr.example/fhir?tenant=1"},
                                {"smart_client_id": "has spaces"}])
def test_launch_settings_that_are_not_safe_are_refused(kw):
    with pytest.raises(InsecureConfiguration):
        Settings(data_dir=DATA, auth_mode="dev", **kw).check()


def test_a_read_needs_no_record_contents_in_its_audit(fake):
    read = launched(smart(fake), fake)
    flat = str(read.audit)
    assert "Mares" not in flat and "1957" not in flat and PATIENT_ID not in flat
    assert all(secret not in flat for secret in fake.issued)
    assert date.fromisoformat(read.as_of) == TODAY
