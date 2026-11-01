"""Opening Caduceus from an EHR: a SMART App Launch against an allow-listed FHIR R4 server (docs/smart-launch.md).

Discovery, PKCE, the code exchange and one bounded read of the launched patient's record run on the server; the
browser only navigates. The access token lives for that one request and is never stored or logged, and the read takes
no more than the approved criteria can use.
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import json
import re
import secrets
import threading
import time
from collections import OrderedDict
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from typing import Any
from urllib.parse import parse_qsl, unquote, urlencode, urlsplit, urlunsplit

import httpx
import jwt

from caduceus.criteria.dsl import AgeRule, AllOf, AnyOf, ConditionRule, CriteriaSet, ObservationRule, ProcedureRule

# The public SMART Health IT sandbox: the demo's default allow-list.
SANDBOX_ISS = ("https://launch.smarthealthit.org/v/r4/fhir",)

FHIR_ID = re.compile(r"[A-Za-z0-9\-.]{1,64}")
STATE = re.compile(r"[A-Za-z0-9_-]{43}")       # secrets.token_urlsafe(32)
OPAQUE = re.compile(r"[\x21-\x7e]{1,2048}")    # a launch or a code, as the EHR made it
LOOPBACK = frozenset({"127.0.0.1", "localhost", "::1"})
STATE_TTL = timedelta(minutes=10)               # from starting a launch to finishing it
MAX_PENDING = 1_000                             # launches started and not yet finished, across every visitor
SEARCHED = ("Condition", "Procedure", "Observation", "DocumentReference")
READ_AS: dict[type, str] = {ConditionRule: "Condition", ProcedureRule: "Procedure", ObservationRule: "Observation"}
FHIR_USER = re.compile(r"(?:Practitioner|PractitionerRole|Patient|RelatedPerson|Person)/[A-Za-z0-9\-.]{1,64}$")


def scopes(v2: bool) -> str:
    """The least this launch needs: its context, who the EHR user is, and reading this one patient's record, in SMART
    v2 permissions when the server offers them."""
    if v2:
        patient = ["patient/Patient.r"] + [f"patient/{t}.rs" for t in SEARCHED]
    else:
        patient = [f"patient/{t}.read" for t in ("Patient", *SEARCHED)]
    return " ".join(["launch", "openid", "fhirUser", *patient])


def granted(scope: str, resource: str, search: bool) -> bool:
    """Whether the granted scopes let Caduceus read (or search) this type for the patient. The server enforces its
    own grant; this only stops Caduceus asking for what it was not given."""
    for s in scope.split():
        m = re.fullmatch(r"(?:patient|user)/(\*|[A-Za-z]+)\.([a-z*]+)(?:\?.*)?", s)
        if not m or m.group(1) not in ("*", resource):
            continue
        perms = m.group(2)
        if perms in ("read", "*") or (re.fullmatch(r"c?r?u?d?s?", perms) and ("s" if search else "r") in perms):
            return True
    return False


def lookback(library: dict[str, CriteriaSet]) -> dict[str, int | None]:
    """The longest look-back any approved criterion uses, for each resource type it reads. None means the type is
    read whole: a diagnosis counts whenever it began. The record is read for these windows and no further."""
    longest: dict[str, int | None] = {}

    def walk(rule: Any) -> None:
        if isinstance(rule, AnyOf | AllOf):
            for r in rule.rules:
                walk(r)
            return
        if isinstance(rule, AgeRule):
            return  # the age comes from the Patient resource, which is always read
        if getattr(rule, "before_procedure", None):
            longest["Procedure"] = None  # "before the test": the test is looked for whenever it was done
        kind = READ_AS[type(rule)]
        days = getattr(rule, "within_days", None)
        if kind in longest and longest[kind] is None:
            return
        longest[kind] = None if days is None else max(days, longest.get(kind) or 0)

    for criteria in library.values():
        for c in criteria.criteria:
            walk(c.rule)
    return longest


def b64url(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode()


def challenge(verifier: str) -> str:
    """PKCE S256: the code challenge for a verifier."""
    return b64url(hashlib.sha256(verifier.encode()).digest())


def normal(url: str) -> str:
    return url.strip().rstrip("/")


def safe_url(url: str) -> bool:
    """An absolute address Caduceus will talk to: https, or plain http to this machine only (the tests' server)."""
    try:
        u = urlsplit(url)
        u.port  # noqa: B018 - a port that is not a number raises here
    except ValueError:
        return False
    if u.username or u.password or u.fragment or not u.hostname:
        return False
    return u.scheme == "https" or (u.scheme == "http" and u.hostname in LOOPBACK)


def _origin(url: str) -> tuple[str, str | None, int | None]:
    u = urlsplit(url)
    return u.scheme, u.hostname, u.port or {"https": 443, "http": 80}.get(u.scheme)


def same_origin(url: str, base: str) -> bool:
    return safe_url(url) and _origin(url) == _origin(base)


def inside(url: str, base: str) -> bool:
    """Whether a URL is on the FHIR server, at or below its base: the only place the token is ever sent. A dot segment
    is refused however it is encoded, since the client resolves "/fhir/../other" to "/other" before sending."""
    if not same_origin(url, base):
        return False
    path, root = urlsplit(url).path, urlsplit(base).path.rstrip("/")
    decoded = path
    for _ in range(3):  # "%252e" is "%2e" once decoded, and "." twice
        decoded = unquote(decoded)
    if any(seg in (".", "..") for seg in decoded.replace("\\", "/").split("/")):
        return False
    return path == root or path.startswith(root + "/")


def with_query(url: str, params: dict[str, str]) -> str:
    u = urlsplit(url)
    return urlunsplit((u.scheme, u.netloc, u.path, urlencode(parse_qsl(u.query) + list(params.items())), ""))


# What went wrong, in words a person can act on. The EHR's own error text never reaches the screen.
AGAIN = "Open Caduceus from the EHR again."
WORDS = {
    "iss_not_allowed": "Caduceus is not set up to open from that EHR, so it did not connect. Open it from the SMART "
                       "launcher on the Help page.",
    "redirect_not_allowed": "Opening from an EHR is not set up for this address.",
    "bad_launch": f"The link from the EHR is incomplete. {AGAIN}",
    "unreachable": "The EHR could not be reached. Try again in a minute.",
    "timeout": "The EHR took too long to answer. Try again in a minute.",
    "discovery_invalid": "The EHR’s sign-in settings could not be read, so Caduceus did not connect.",
    "no_pkce": "The EHR does not offer the sign-in protection Caduceus requires (PKCE), so it did not connect.",
    "state_unknown": f"This sign-in has already been used, or it did not start here. {AGAIN}",
    "state_expired": f"This sign-in took longer than 10 minutes, so it expired. {AGAIN}",
    "bad_code": f"The EHR’s answer was incomplete. {AGAIN}",
    "token_refused": f"The EHR did not accept the sign-in. {AGAIN}",
    "not_shared": f"The EHR did not share this patient’s record with Caduceus. {AGAIN} If it asks, allow access to "
                  "the record.",
    "not_found": f"The EHR has no record for the patient it launched. {AGAIN}",
    "too_large": "This patient’s record is larger than Caduceus reads at once, so nothing was saved. Open a "
                 "different patient.",
    "bad_response": "The EHR sent something Caduceus could not read, so nothing was saved. Try again, or open a "
                    "different patient.",
    "incomplete_patient": "The EHR’s record for this patient has no name or no full date of birth, which a "
                          "prior-authorization request needs. Open a different patient.",
}
STATUS = {"iss_not_allowed": 400, "redirect_not_allowed": 400, "bad_launch": 400, "state_unknown": 400,
          "state_expired": 400, "bad_code": 400, "timeout": 504, "too_large": 422}


class LaunchRefused(Exception):
    """A launch that cannot go on. `reason` is a fixed code for the audit log; `detail` is what the audit log may keep
    about it, which is never a token, a code or anything from the record."""

    def __init__(self, reason: str, **detail: Any):
        super().__init__(WORDS[reason])
        self.reason, self.detail = reason, detail
        self.status = STATUS.get(reason, 502)

    @property
    def message(self) -> str:
        return str(self)

    @property
    def audit(self) -> dict[str, Any]:
        return {"reason": self.reason} | self.detail


@dataclass(frozen=True)
class Limits:
    timeout: float = 10.0       # seconds for any one request to the EHR
    deadline: float = 20.0      # seconds for the code exchange and every read together (Netlify allows 26)
    resources: int = 1_000      # entries read across every search
    bytes: int = 3_000_000      # bytes read across every response
    page_size: int = 100        # _count asked for on each search
    pages: int = 20             # pages followed for any one search
    notes: int = 5              # newest notes kept, as the case's pages
    note_bytes: int = 100_000   # a longer note is not kept as a page
    small_bytes: int = 64_000   # the SMART configuration, or the token endpoint's answer


@dataclass(frozen=True)
class Server:
    iss: str
    authorize: str
    token: str
    v2: bool


@dataclass
class Pending:
    """A launch started and not yet finished, bound to who started it and where. Kept in memory only, and used
    once."""
    state: str
    server: Server
    verifier: str = field(repr=False)
    redirect_uri: str
    scope: str
    sandbox: str | None
    user: str
    started: datetime


@dataclass
class Grant:
    """What the token endpoint gave. Lives for the callback request only; never stored, never logged."""
    token: str = field(repr=False)
    patient: str
    scope: str
    ehr_user: str | None


@dataclass
class Read:
    """One patient's record, as read: what is filed with the cases, and what the audit log keeps about the read."""
    iss: str
    match: str                  # the server and its id for the patient, hashed
    patient: dict[str, Any]
    resources: list[dict[str, Any]]
    notes: list[dict[str, Any]]
    counts: dict[str, int]
    not_shared: list[str]
    windows: dict[str, int | None]
    as_of: str
    audit: dict[str, Any]


class Budget:
    """What one launch may still read, and by when."""

    def __init__(self, limits: Limits, clock: Callable[[], float]):
        self.resources, self.bytes = limits.resources, limits.bytes
        self.clock, self.until, self.timeout = clock, clock() + limits.deadline, limits.timeout
        self.requests = 0

    def left(self) -> float:
        """Seconds the next request may take. Raises once the launch's time is up."""
        left = self.until - self.clock()
        if left <= 0:
            raise LaunchRefused("timeout")
        return min(self.timeout, left)

    def entries(self, n: int) -> None:
        self.resources -= n
        if self.resources < 0:
            raise LaunchRefused("too_large", limit="resources")


def _dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _string(value: Any) -> str | None:
    return value if isinstance(value, str) else None


def _strings(value: Any) -> list[str]:
    return [v for v in _list(value) if isinstance(v, str)]


def _clean_patient(p: dict[str, Any]) -> dict[str, Any] | None:
    """The patient with only the fields Caduceus reads, each only where it has FHIR's shape, among them the contact
    details and identifiers that de-identification removes. None without a usable name and a full date of birth."""
    def entries(key: str, fields: dict[str, Callable[[Any], Any]]) -> list[dict[str, Any]]:
        out = []
        for item in p.get(key) or []:
            if isinstance(item, dict):
                kept = {k: f(item[k]) for k, f in fields.items() if k in item and f(item[k]) not in (None, [], "")}
                if kept:
                    out.append(kept)
        return out

    names = [n for n in entries("name", {"use": _string, "family": _string, "given": _strings, "text": _string})
             if n.get("family") or n.get("given")]
    born = p.get("birthDate")
    if not names or not isinstance(born, str) or not re.fullmatch(r"\d{4}-\d{2}-\d{2}", born):
        return None
    try:
        if date.fromisoformat(born) > date.today():
            return None
    except ValueError:
        return None
    out = {"resourceType": "Patient", "id": p["id"], "name": names, "birthDate": born,
            "telecom": entries("telecom", {"system": _string, "value": _string, "use": _string}),
            "address": entries("address", {"line": _strings, "city": _string, "state": _string,
                                           "postalCode": _string}),
            "identifier": entries("identifier", {"system": _string, "value": _string})}
    if p.get("gender") in ("male", "female", "other", "unknown"):
        out["gender"] = p["gender"]
    return out


def _about(resource: dict[str, Any], patient: str) -> bool:
    subject = resource.get("subject")
    ref = subject.get("reference") if isinstance(subject, dict) else None
    return isinstance(ref, str) and (ref == f"Patient/{patient}" or ref.endswith(f"/Patient/{patient}"))


def _kept(resource: dict[str, Any]) -> dict[str, Any]:
    """The resource without its narrative and server metadata, which are never used."""
    return {k: v for k, v in resource.items() if k not in ("text", "meta")}


def _when(doc: dict[str, Any]) -> str:
    period = _dict(_dict(doc.get("context")).get("period"))
    when = doc.get("date") or period.get("start") or ""
    return when if isinstance(when, str) else ""


def note_title(doc: dict[str, Any]) -> str:
    kind = _dict(doc.get("type"))
    codings = _list(kind.get("coding"))
    title = doc.get("description") or kind.get("text") or next(
        (c.get("display") for c in codings if isinstance(c, dict) and c.get("display")), None)
    return str(title)[:120] if isinstance(title, str) and title.strip() else "Note from the EHR"


def note_text(doc: dict[str, Any], limit: int) -> str | None:
    """A note's text, when the EHR sent it inline as plain text; None when there is none Caduceus can show."""
    for c in _list(doc.get("content")):
        att = _dict(c).get("attachment")
        if not isinstance(att, dict) or not isinstance(att.get("data"), str):
            continue
        kind = att.get("contentType")
        if not isinstance(kind, str):
            kind = "text/plain"
        if kind.split(";")[0].strip().lower() != "text/plain":
            continue
        try:
            raw = base64.b64decode(att["data"], validate=True)
        except (binascii.Error, ValueError):
            continue
        if 0 < len(raw) <= limit:
            return raw.decode("utf-8", errors="replace")
    return None


def page_of(doc: dict[str, Any], text: str) -> dict[str, Any]:
    """A note as a page of the case: built from scratch, with only what the page shows."""
    authors = _list(doc.get("author"))
    author = next((a["display"] for a in authors if isinstance(a, dict) and isinstance(a.get("display"), str)), None)
    return {"resourceType": "DocumentReference", "id": doc["id"], "description": note_title(doc),
            **({"date": _when(doc)} if _when(doc) else {}), **({"author": [{"display": author}]} if author else {}),
            "content": [{"attachment": {"contentType": "text/plain; charset=utf-8",
                                        "data": base64.b64encode(text.encode()).decode()}}]}


class SmartLaunch:
    """The EHR launch, start to finish. One per API process; safe to use from any request thread."""

    def __init__(self, allowed: Iterable[str], redirect_uris: Iterable[str], client_id: str,
                 transport: httpx.BaseTransport | None = None, limits: Limits | None = None,
                 clock: Callable[[], float] = time.monotonic):
        self.allowed = frozenset(normal(u) for u in allowed)
        self.redirect_uris = frozenset(redirect_uris)
        self.client_id = client_id
        self.limits = limits or Limits()
        self._transport, self._clock = transport, clock
        self._pending: OrderedDict[str, Pending] = OrderedDict()
        self._lock = threading.Lock()

    @property
    def enabled(self) -> bool:
        return bool(self.allowed and self.redirect_uris and self.client_id)

    def _client(self) -> httpx.Client:
        # No redirects are followed: an answer that points somewhere else is refused, not chased.
        return httpx.Client(transport=self._transport, timeout=self.limits.timeout, follow_redirects=False)

    # Starting -------------------------------------------------------------------------------------------------

    def begin(self, iss: str, launch: str, redirect_uri: str, sandbox: str | None, user: str,
              now: datetime) -> tuple[Pending, str]:
        """Check the launch, discover the EHR's endpoints, and return the address the browser goes to next."""
        iss = normal(iss)
        if iss not in self.allowed:
            raise LaunchRefused("iss_not_allowed", iss=iss[:200])
        if redirect_uri not in self.redirect_uris:
            raise LaunchRefused("redirect_not_allowed")
        if not OPAQUE.fullmatch(launch):
            raise LaunchRefused("bad_launch")
        server = self.discover(iss)
        verifier = secrets.token_urlsafe(64)  # 86 characters, within PKCE's 43 to 128
        pending = Pending(state=secrets.token_urlsafe(32), server=server, verifier=verifier,
                          redirect_uri=redirect_uri, scope=scopes(server.v2), sandbox=sandbox, user=user, started=now)
        self._remember(pending)
        url = with_query(server.authorize, {
            "response_type": "code", "client_id": self.client_id, "redirect_uri": redirect_uri, "launch": launch,
            "scope": pending.scope, "state": pending.state, "aud": iss,
            "code_challenge": challenge(verifier), "code_challenge_method": "S256"})
        return pending, url

    def discover(self, iss: str) -> Server:
        """The EHR's SMART configuration. Its authorization and token endpoints must be on the FHIR server's own
        origin, so a changed configuration cannot send the code anywhere else."""
        budget = Budget(Limits(bytes=self.limits.small_bytes, deadline=self.limits.timeout,
                               timeout=self.limits.timeout), self._clock)
        with self._client() as client:
            cfg = self._json(client, f"{iss}/.well-known/smart-configuration", {"Accept": "application/json"},
                             budget, invalid="discovery_invalid")
        authorize, token = cfg.get("authorization_endpoint"), cfg.get("token_endpoint")
        if not isinstance(authorize, str) or not isinstance(token, str) or \
                not all(len(u) <= 512 and same_origin(u, iss) for u in (authorize, token)):
            raise LaunchRefused("discovery_invalid")
        methods = cfg.get("code_challenge_methods_supported")
        if methods is not None and (not isinstance(methods, list) or "S256" not in methods):
            raise LaunchRefused("no_pkce")
        caps = cfg.get("capabilities")
        return Server(iss=iss, authorize=authorize, token=token, v2=isinstance(caps, list) and "permission-v2" in caps)

    def _remember(self, pending: Pending) -> None:
        with self._lock:
            for state in [s for s, p in self._pending.items() if pending.started - p.started > STATE_TTL]:
                del self._pending[state]
            self._pending[pending.state] = pending
            while len(self._pending) > MAX_PENDING:
                self._pending.popitem(last=False)

    def pending(self) -> int:
        with self._lock:
            return len(self._pending)

    # Finishing ------------------------------------------------------------------------------------------------

    def finish(self, code: str, state: str, sandbox: str | None, user: str, now: datetime, as_of: date,
               windows: dict[str, int | None]) -> Read:
        """Check the state, exchange the code, and read the record. The state is spent first, so it is single use
        however the rest goes. The grant, and its token, go out of scope when this returns."""
        with self._lock:
            pending = self._pending.pop(state, None) if STATE.fullmatch(state) else None
        if pending is None:
            raise LaunchRefused("state_unknown")
        if (pending.sandbox, pending.user) != (sandbox, user):
            raise LaunchRefused("state_unknown", why="started by someone else or in another copy")
        if now - pending.started > STATE_TTL:
            raise LaunchRefused("state_expired")
        if not OPAQUE.fullmatch(code):
            raise LaunchRefused("bad_code")
        budget = Budget(self.limits, self._clock)
        with self._client() as client:
            grant = self._exchange(client, pending, code, budget)
            return self._read(client, pending, grant, budget, as_of, windows)

    def _exchange(self, client: httpx.Client, pending: Pending, code: str, budget: Budget) -> Grant:
        form = {"grant_type": "authorization_code", "code": code, "redirect_uri": pending.redirect_uri,
                "client_id": self.client_id, "code_verifier": pending.verifier}
        status, raw = self._fetch(client, "POST", pending.server.token, {"Accept": "application/json"}, budget,
                                  cap=self.limits.small_bytes, data=form)
        try:
            body = json.loads(raw) if raw else None
        except ValueError:
            body = None
        if status != 200:
            said = body.get("error") if isinstance(body, dict) else None
            ok = isinstance(said, str) and re.fullmatch(r"[a-z_]{1,40}", said)
            raise LaunchRefused("token_refused", status=status, oauth_error=said if ok else None)
        if not isinstance(body, dict):
            raise LaunchRefused("bad_response", step="token")
        token, patient, kind = body.get("access_token"), body.get("patient"), body.get("token_type")
        if not isinstance(token, str) or not 0 < len(token) <= 8192 or not isinstance(kind, str) \
                or kind.lower() != "bearer":
            raise LaunchRefused("bad_response", step="token")
        if not isinstance(patient, str) or not FHIR_ID.fullmatch(patient):
            raise LaunchRefused("not_shared", why="no patient in the launch context")
        scope = body.get("scope")
        if not isinstance(scope, str):
            scope = pending.scope
        return Grant(token=token, patient=patient, scope=scope[:2000], ehr_user=self._ehr_user(body.get("id_token")))

    def _ehr_user(self, id_token: Any) -> str | None:
        """Who the EHR says launched Caduceus (its fhirUser), for the audit log only: it grants nothing. The ID token
        came straight from the token endpoint over TLS, which OpenID Connect accepts in place of a signature check
        (Core 3.1.3.7); its audience and expiry are still checked, and anything amiss is ignored."""
        if not isinstance(id_token, str) or len(id_token) > 16_384:
            return None
        try:
            claims = jwt.decode(id_token, options={"verify_signature": False})
        except jwt.PyJWTError:
            return None
        aud = claims.get("aud")
        exp = claims.get("exp")
        if self.client_id not in (aud if isinstance(aud, list) else [aud]) \
                or not isinstance(exp, int | float) or exp < time.time():
            return None
        who = claims.get("fhirUser")
        m = FHIR_USER.search(who) if isinstance(who, str) and len(who) < 300 else None
        return m.group(0) if m else None

    def _read(self, client: httpx.Client, pending: Pending, grant: Grant, budget: Budget, as_of: date,
              windows: dict[str, int | None]) -> Read:
        iss, pid = pending.server.iss, grant.patient
        auth = {"Authorization": f"Bearer {grant.token}", "Accept": "application/fhir+json"}
        if not granted(grant.scope, "Patient", search=False):
            raise LaunchRefused("not_shared", why="Patient not granted")
        found = self._json(client, f"{iss}/Patient/{pid}", auth, budget)
        budget.entries(1)
        if found.get("resourceType") != "Patient" or found.get("id") != pid:
            raise LaunchRefused("bad_response", step="Patient")
        patient = _clean_patient(found)
        if patient is None:
            raise LaunchRefused("incomplete_patient")
        resources: list[dict[str, Any]] = []
        docs: list[dict[str, Any]] = []
        counts: dict[str, int] = {}
        not_shared, dropped = [], 0
        for kind in SEARCHED:
            if not granted(grant.scope, kind, search=True):
                not_shared.append(kind)
                continue
            params = {"patient": pid, "_count": str(self.limits.page_size)}
            window = windows.get(kind)
            if kind == "DocumentReference":  # one page, newest first: the notes a case shows, not the whole history
                params |= {"_count": str(self.limits.notes * 2), "_sort": "-date"}
            elif window is not None:
                params["date"] = f"ge{(as_of - timedelta(days=window)).isoformat()}"
            kept, gone = self._search(client, with_query(f"{iss}/{kind}", params), kind, pid, auth, budget, iss,
                                      follow=kind != "DocumentReference")
            dropped += gone
            counts[kind] = len(kept)
            (docs if kind == "DocumentReference" else resources).extend(kept)
        notes: list[dict[str, Any]] = []
        for doc in sorted(docs, key=_when, reverse=True):
            text = note_text(doc, self.limits.note_bytes)
            if text is not None and len(notes) < self.limits.notes:
                notes.append(page_of(doc, text))
        audit = {"iss": iss, "resources": counts, "notes": len(notes), "not_shared": not_shared,
                 "dropped": dropped, "requests": budget.requests, "bytes": self.limits.bytes - budget.bytes,
                 "scope": grant.scope, "ehr_user": grant.ehr_user}
        return Read(iss=iss, match=hashlib.sha256(f"{iss}|{pid}".encode()).hexdigest(), patient=patient,
                    resources=resources, notes=notes, counts=counts, not_shared=not_shared,
                    windows={k: windows.get(k) for k in ("Procedure", "Observation")}, as_of=as_of.isoformat(),
                    audit=audit)

    def _search(self, client: httpx.Client, url: str, kind: str, pid: str, auth: dict[str, str], budget: Budget,
                iss: str, follow: bool) -> tuple[list[dict[str, Any]], int]:
        """Every page of one search, following the server's next links only while they stay on the FHIR server.
        Entries about anyone but the launched patient are dropped and counted."""
        found, dropped, seen = [], 0, {url}
        for _ in range(self.limits.pages):
            bundle = self._json(client, url, auth, budget)
            if bundle.get("resourceType") != "Bundle":
                raise LaunchRefused("bad_response", step=kind)
            entries = [e for e in bundle.get("entry") or [] if isinstance(e, dict)]
            budget.entries(len(entries))
            for e in entries:
                r = e.get("resource")
                if not isinstance(r, dict) or r.get("resourceType") == "OperationOutcome" \
                        or _dict(e.get("search")).get("mode") == "outcome":
                    continue
                if r.get("resourceType") != kind or not FHIR_ID.fullmatch(str(r.get("id", ""))) or not _about(r, pid):
                    dropped += 1
                    continue
                found.append(_kept(r))
            nxt = next((link.get("url") for link in _list(bundle.get("link"))
                        if isinstance(link, dict) and link.get("relation") == "next"), None)
            if not follow or not nxt:
                return found, dropped
            if not isinstance(nxt, str) or not inside(nxt, iss) or nxt in seen:
                raise LaunchRefused("bad_response", step=f"{kind} paging")
            seen.add(nxt)
            url = nxt
        raise LaunchRefused("too_large", limit="pages")

    def _json(self, client: httpx.Client, url: str, headers: dict[str, str], budget: Budget,
              invalid: str = "bad_response") -> dict[str, Any]:
        """GET one JSON object, within the budget's time and bytes."""
        status, raw = self._fetch(client, "GET", url, headers, budget)
        if status in (401, 403):
            raise LaunchRefused("not_shared", status=status)
        if status == 404:
            raise LaunchRefused("not_found" if invalid == "bad_response" else invalid, status=404)
        if status != 200:
            raise LaunchRefused(invalid, status=status)
        try:
            data = json.loads(raw)
        except ValueError as e:
            raise LaunchRefused(invalid) from e
        if not isinstance(data, dict):
            raise LaunchRefused(invalid)
        return data

    def _fetch(self, client: httpx.Client, method: str, url: str, headers: dict[str, str], budget: Budget,
               cap: int | None = None, data: dict[str, str] | None = None) -> tuple[int, bytes]:
        """One request, its answer read as it streams in and cut off at the byte budget (or `cap`) or the deadline."""
        try:
            with client.stream(method, url, headers=headers, data=data, timeout=budget.left()) as r:
                budget.requests += 1
                limit = min(budget.bytes, cap) if cap is not None else budget.bytes
                declared = r.headers.get("content-length", "")
                if declared.isdigit() and int(declared) > limit:
                    raise LaunchRefused("too_large", limit="bytes")
                chunks, size = [], 0
                for chunk in r.iter_bytes():
                    size += len(chunk)
                    if size > limit:
                        raise LaunchRefused("too_large", limit="bytes")
                    budget.left()  # a server that trickles its answer still runs out of time
                    chunks.append(chunk)
                budget.bytes -= size
                return r.status_code, b"".join(chunks)
        except httpx.TimeoutException as e:
            raise LaunchRefused("timeout") from e
        except httpx.HTTPError as e:
            raise LaunchRefused("unreachable", error=type(e).__name__) from e
