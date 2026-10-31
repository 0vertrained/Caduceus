"""A SMART on FHIR R4 server in miniature, standing in for the public SMART launcher so no test calls it.

It does what the launcher does for an EHR launch: publishes its SMART configuration, authorizes (checking the
client, the redirect URI, the audience, the launch and the PKCE challenge, then redirecting back with a code), and
exchanges the code for a token only with the matching PKCE verifier, once. Its FHIR API answers only with a valid
token, only for the launched patient, pages its searches the way HAPI does, and honors `date=ge` and `_sort=-date`.

The API tests use it through httpx.MockTransport (`FakeSmart(...).transport()`); the end-to-end walk runs it over
HTTP on a loopback port:

    python -m tests.fake_smart --port 8766      # FHIR base http://127.0.0.1:8766/v/r4/fhir
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import json
import secrets
import time
from dataclasses import dataclass, field
from datetime import date, timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any
from urllib.parse import parse_qs, urlencode, urlsplit

import httpx
import jwt

PATIENT_ID = "6c5d2e88-5a1f-4b6f-9a77-2f1c0e3d9b41"
PRACTITIONER = "Practitioner/fake-practitioner-7"
SNOMED, LOINC = "http://snomed.info/sct", "http://loinc.org"


def _challenge(verifier: str) -> str:
    return base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()


def _day(as_of: date, days_ago: int) -> str:
    return (as_of - timedelta(days=days_ago)).isoformat()


def patient(pid: str = PATIENT_ID) -> dict[str, Any]:
    return {"resourceType": "Patient", "id": pid, "meta": {"versionId": "3"},
            "text": {"status": "generated", "div": "<div>Evelyn Mares</div>"},
            "identifier": [{"system": "http://hospital.example/mrn", "value": "MR-55821934"},
                           {"system": "http://hl7.org/fhir/sid/us-ssn", "value": "999-41-5521"}],
            "name": [{"use": "official", "family": "Mares", "given": ["Evelyn", "Rose"], "prefix": ["Mrs."]}],
            "telecom": [{"system": "phone", "value": "505-555-0144", "use": "home"}],
            "gender": "female", "birthDate": "1957-04-12",
            "address": [{"line": ["418 Calle Lorenzo"], "city": "Albuquerque", "state": "NM", "postalCode": "87106"}],
            "maritalStatus": {"text": "Married"}}


def _concept(system: str, code: str, display: str) -> dict[str, Any]:
    return {"coding": [{"system": system, "code": code, "display": display}], "text": display}


def record(as_of: date, pid: str = PATIENT_ID) -> list[dict[str, Any]]:
    """A Synthea-like record, as the sandbox's are: SNOMED and LOINC codes, none of this project's local codes, so
    most criteria stay unanswered. Dates are relative to `as_of`, some inside the look-back windows and some not."""
    subject = {"reference": f"Patient/{pid}"}
    out: list[dict[str, Any]] = []

    def condition(n: int, code: str, display: str, onset: str, abated: str | None = None) -> None:
        status = "resolved" if abated else "active"
        out.append({"resourceType": "Condition", "id": f"cond-{n}", "subject": subject,
                    "clinicalStatus": {"coding": [{"code": status}]},
                    "verificationStatus": {"coding": [{"code": "confirmed"}]},
                    "code": _concept(SNOMED, code, display), "onsetDateTime": onset,
                    **({"abatementDateTime": abated} if abated else {})})

    condition(1, "278860009", "Chronic low back pain (finding)", "2019-03-14")
    condition(2, "239873007", "Osteoarthritis of knee", "2021-06-02")
    condition(3, "59621000", "Essential hypertension (disorder)", "2008-09-20")
    condition(4, "15777000", "Prediabetes", "2016-05-11")
    condition(5, "10509002", "Acute bronchitis (disorder)", _day(as_of, 600), _day(as_of, 585))
    condition(6, "314529007", "Medication review due (situation)", _day(as_of, 40))

    def procedure(n: int, code: str, display: str, days_ago: int) -> None:
        when = _day(as_of, days_ago)
        out.append({"resourceType": "Procedure", "id": f"proc-{n}", "subject": subject, "status": "completed",
                    "code": _concept(SNOMED, code, display), "performedPeriod": {"start": when, "end": when}})

    for n, ago in enumerate((40, 220, 400), start=1):
        procedure(n, "430193006", "Medication reconciliation (procedure)", ago)
    procedure(4, "74016001", "Plain X-ray of knee", 120)
    procedure(5, "91251008", "Physical therapy procedure", 1300)  # before the two-year window: never asked for

    vitals = [("8867-4", "Heart rate", 72, "/min"), ("29463-7", "Body weight", 81.2, "kg"),
              ("39156-5", "Body mass index", 30.4, "kg/m2"), ("72514-3", "Pain severity - 0-10 verbal numeric "
                                                                        "rating [Score] - Reported", 6, "{score}")]
    n = 0
    for ago in (30, 75, 130, 190, 250, 310, 500):  # the last visit is outside the one-year window
        for code, display, value, unit in vitals:
            n += 1
            out.append({"resourceType": "Observation", "id": f"obs-{n}", "subject": subject, "status": "final",
                        "code": _concept(LOINC, code, display), "effectiveDateTime": _day(as_of, ago),
                        "valueQuantity": {"value": value, "unit": unit}})

    notes = [(30, "Progress note", "Follow-up for chronic low back pain. Pain 6 of 10, worse with standing. Home "
                                   "exercises continued. Knee pain on stairs."),
             (75, "Progress note", "Blood pressure recheck. Back pain unchanged."),
             (130, "History and physical note", "Annual visit. Chronic low back pain since 2019; osteoarthritis of "
                                                "the right knee. No fever or weight loss."),
             (190, "Progress note", "Knee X-ray reviewed with the patient."),
             (250, "Progress note", "Medication reconciliation."),
             (310, "Progress note", "Seen for bronchitis follow-up; resolved."),
             (500, "Progress note", "Older visit.")]
    for i, (ago, kind, text) in enumerate(notes, start=1):
        out.append({"resourceType": "DocumentReference", "id": f"doc-{i}", "subject": subject, "status": "current",
                    "type": _concept(LOINC, "11506-3", kind), "date": f"{_day(as_of, ago)}T15:20:00-06:00",
                    "author": [{"display": "Dr. Lucia Ortiz"}],
                    "content": [{"attachment": {"contentType": "text/plain; charset=utf-8",
                                                "data": base64.b64encode(text.encode()).decode()}}]})
    out.append({"resourceType": "DocumentReference", "id": "doc-html", "subject": subject, "status": "current",
                "date": f"{_day(as_of, 20)}T10:00:00-06:00", "type": {"text": "Scanned letter"},
                "content": [{"attachment": {"contentType": "application/pdf", "url": "Binary/letter-1"}}]})
    return out


@dataclass
class FakeSmart:
    base: str = "https://ehr.example/v/r4/fhir"
    client_id: str = "caduceus-demo"
    redirect_uris: tuple[str, ...] = ()       # empty: any redirect URI, as the launcher allows unless told otherwise
    v2: bool = True
    page_size: int = 50                        # the most it returns per page, whatever _count asks for
    as_of: date = field(default_factory=date.today)
    resources: list[dict[str, Any]] | None = None
    patient_resource: dict[str, Any] | None = None
    grant: str | None = None                   # the scope it grants; None grants what was asked
    discovery: dict[str, Any] | None = None    # replaces the published configuration
    token_status: int = 200
    id_token_audience: str | None = None       # an ID token meant for another app; None: for this client
    next_link_base: str | None = None          # page links on another host, as a hostile server might send
    fail: dict[str, Exception] = field(default_factory=dict)  # path suffix -> exception raised for it
    requests: list[httpx.Request] = field(default_factory=list)
    codes: dict[str, dict[str, Any]] = field(default_factory=dict)
    tokens: dict[str, dict[str, Any]] = field(default_factory=dict)
    searches: dict[str, list[dict[str, Any]]] = field(default_factory=dict)
    issued: list[str] = field(default_factory=list)  # every code, access token and ID token it handed out

    def __post_init__(self) -> None:
        u = urlsplit(self.base)
        self.origin = f"{u.scheme}://{u.netloc}"
        self.auth_base = self.origin + u.path.rsplit("/", 1)[0] + "/auth"  # .../v/r4/auth, as the launcher has it
        if self.resources is None:
            self.resources = record(self.as_of)
        if self.patient_resource is None:
            self.patient_resource = patient()

    def transport(self) -> httpx.MockTransport:
        return httpx.MockTransport(self.handle)

    # Helpers for tests ----------------------------------------------------------------------------------------

    def authorize(self, url: str) -> tuple[str, str]:
        """Follow the authorize URL as a browser would; returns the code and state it redirects back with."""
        r = self.handle(httpx.Request("GET", url))
        assert r.status_code == 302, r.text
        q = parse_qs(urlsplit(r.headers["location"]).query)
        return q["code"][0], q["state"][0]

    def hits(self, suffix: str) -> int:
        return sum(1 for r in self.requests if r.url.path.endswith(suffix))

    # The server -----------------------------------------------------------------------------------------------

    def handle(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        path = request.url.path
        for suffix, error in self.fail.items():
            if path.endswith(suffix):
                raise error
        if f"{request.url.scheme}://{request.url.netloc.decode()}" != self.origin:
            return httpx.Response(404)
        root = urlsplit(self.base).path
        if path == f"{root}/.well-known/smart-configuration":
            return httpx.Response(200, json=self.configuration())
        if path == urlsplit(self.auth_base).path + "/authorize":
            return self._authorize(request)
        if path == urlsplit(self.auth_base).path + "/token":
            return self._token(request)
        if path == root or path.startswith(root + "/"):
            return self._fhir(request, path[len(root):].strip("/"))
        return httpx.Response(404)

    def configuration(self) -> dict[str, Any]:
        if self.discovery is not None:
            return self.discovery
        caps = ["launch-ehr", "client-public", "context-ehr-patient", "sso-openid-connect", "permission-v1"]
        return {"authorization_endpoint": self.auth_base + "/authorize", "token_endpoint": self.auth_base + "/token",
                "capabilities": caps + (["permission-v2"] if self.v2 else []),
                "code_challenge_methods_supported": ["S256"], "grant_types_supported": ["authorization_code"],
                "response_types_supported": ["code"], "scopes_supported": ["openid", "fhirUser", "launch"]}

    def _authorize(self, request: httpx.Request) -> httpx.Response:
        q = {k: v[0] for k, v in parse_qs(request.url.query.decode()).items()}
        problems = [
            q.get("response_type") != "code" and "response_type",
            q.get("client_id") != self.client_id and "client_id",
            self.redirect_uris and q.get("redirect_uri") not in self.redirect_uris and "redirect_uri",
            q.get("aud") != self.base and "aud",
            not q.get("launch") and "launch",
            not q.get("state") and "state",
            q.get("code_challenge_method") != "S256" and "code_challenge_method",
            len(q.get("code_challenge", "")) != 43 and "code_challenge"]
        if any(problems):
            return httpx.Response(400, json={"error": "invalid_request", "problems": [p for p in problems if p]})
        code = secrets.token_urlsafe(24)
        self.codes[code] = q
        self.issued.append(code)
        return httpx.Response(302, headers={"location": q["redirect_uri"] + "?" + urlencode(
            {"code": code, "state": q["state"]})})

    def _token(self, request: httpx.Request) -> httpx.Response:
        form = {k: v[0] for k, v in parse_qs(request.content.decode()).items()}
        asked = self.codes.pop(form.get("code", ""), None)  # a code is good once
        if self.token_status != 200:
            return httpx.Response(self.token_status, json={
                "error": "invalid_grant", "error_description": "Internal trace: key /srv/keys/launcher.pem id 7"})
        if asked is None or form.get("grant_type") != "authorization_code" \
                or form.get("redirect_uri") != asked["redirect_uri"] or form.get("client_id") != self.client_id:
            return httpx.Response(400, json={"error": "invalid_grant"})
        if _challenge(form.get("code_verifier", "")) != asked["code_challenge"]:
            return httpx.Response(401, json={"error": "invalid_grant", "error_description": "Invalid PKCE verifier"})
        scope = self.grant if self.grant is not None else asked["scope"]
        token = secrets.token_urlsafe(32)
        self.tokens[token] = {"scope": scope, "patient": self.patient_resource["id"]}  # type: ignore[index]
        id_token = jwt.encode({"iss": self.base, "aud": self.id_token_audience or self.client_id, "sub": "a1b2c3",
                               "fhirUser": PRACTITIONER,
                               "iat": int(time.time()), "exp": int(time.time()) + 3600},
                              "fake-launcher-signing-key-at-least-32-bytes", algorithm="HS256")
        self.issued += [token, id_token]
        return httpx.Response(200, json={"access_token": token, "token_type": "Bearer", "expires_in": 3600,
                                         "scope": scope, "patient": self.patient_resource["id"],  # type: ignore[index]
                                         "id_token": id_token, "need_patient_banner": True})

    def _fhir(self, request: httpx.Request, rest: str) -> httpx.Response:
        auth = request.headers.get("authorization", "")
        grant = self.tokens.get(auth.removeprefix("Bearer ")) if auth.startswith("Bearer ") else None
        if grant is None:
            return httpx.Response(401, json={"resourceType": "OperationOutcome"})
        q = {k: v[0] for k, v in parse_qs(request.url.query.decode()).items()}
        if not rest and "_getpages" in q:
            return self._page(q["_getpages"], int(q.get("_getpagesoffset", "0")), int(q.get("_count", "0")) or None)
        kind, _, rid = rest.partition("/")
        if not (f"patient/{kind}." in grant["scope"] or "patient/*." in grant["scope"]):
            return httpx.Response(403, json={"resourceType": "OperationOutcome"})
        if kind == "Patient" and rid:
            p = self.patient_resource
            return httpx.Response(200, json=p) if rid == p["id"] == grant["patient"] else httpx.Response(404)  # type: ignore[index]
        if q.get("patient") != grant["patient"]:
            return httpx.Response(403, json={"resourceType": "OperationOutcome"})
        found = [r for r in self.resources if r["resourceType"] == kind]  # type: ignore[union-attr]
        since = q.get("date", "")
        if since.startswith("ge"):
            found = [r for r in found if self._date(r) >= since[2:]]
        if q.get("_sort") == "-date":
            found.sort(key=self._date, reverse=True)
        search = secrets.token_hex(6)
        self.searches[search] = found
        return self._page(search, 0, int(q.get("_count", self.page_size)))

    def _page(self, search: str, offset: int, count: int | None = None) -> httpx.Response:
        found = self.searches.get(search)
        if found is None:
            return httpx.Response(410, json={"resourceType": "OperationOutcome"})
        size = min(count or self.page_size, self.page_size)
        bundle: dict[str, Any] = {"resourceType": "Bundle", "type": "searchset", "total": len(found),
                                  "entry": [{"fullUrl": f"{self.base}/{r['resourceType']}/{r['id']}", "resource": r,
                                             "search": {"mode": "match"}} for r in found[offset:offset + size]],
                                  "link": [{"relation": "self", "url": self.base}]}
        if offset + size < len(found):
            base = self.next_link_base or self.base
            bundle["link"].append({"relation": "next", "url": f"{base}?" + urlencode(
                {"_getpages": search, "_getpagesoffset": offset + size, "_count": size, "_bundletype": "searchset"})})
        return httpx.Response(200, json=bundle, headers={"content-type": "application/fhir+json"})

    @staticmethod
    def _date(r: dict[str, Any]) -> str:
        return (r.get("effectiveDateTime") or (r.get("performedPeriod") or {}).get("start") or r.get("date")
                or r.get("onsetDateTime") or "")[:10]


def serve(port: int) -> None:
    """The fake over HTTP, for the end-to-end walk. Loopback only."""
    fake = FakeSmart(base=f"http://127.0.0.1:{port}/v/r4/fhir")

    class Handler(BaseHTTPRequestHandler):
        def _answer(self) -> None:
            body = self.rfile.read(int(self.headers.get("content-length") or 0))
            request = httpx.Request(self.command, f"http://127.0.0.1:{port}{self.path}",
                                    headers=dict(self.headers.items()), content=body)
            response = fake.handle(request)
            self.send_response(response.status_code)
            for k, v in response.headers.items():
                if k.lower() not in ("content-length", "transfer-encoding", "connection"):
                    self.send_header(k, v)
            self.send_header("content-length", str(len(response.content)))
            self.end_headers()
            self.wfile.write(response.content)

        do_GET = do_POST = _answer  # noqa: N815 - the names http.server dispatches to

        def log_message(self, *args: Any) -> None:  # quiet: the walk's report is the output
            pass

    ThreadingHTTPServer(("127.0.0.1", port), Handler).serve_forever()


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--port", type=int, default=8766)
    serve(ap.parse_args().port)
    print(json.dumps({"stopped": True}))
