"""Rule-based de-identification following the HIPAA Safe Harbor method, plus tamper-evident stamps.

Safe Harbor removes 18 categories of identifiers of the individual (and of relatives, employers and household
members) and requires no actual knowledge that what remains could identify them. This module removes:

* identifiers the system already knows for the case (names, date of birth, address, phone, email, MRN, member
  and account numbers, SSN) wherever they appear, and
* pattern matches for the categories that can appear in free text without being known in advance (dates,
  phone and fax numbers, email addresses, SSNs, labelled record and plan numbers, URLs, IP addresses, street
  addresses, ZIP codes next to a state, and ages over 89).

Each removed value is replaced with a numbered token such as ``[NAME_1]``. The token map stays inside the VPC so
a draft written over de-identified text can be re-identified locally. A residual check refuses to stamp any text
in which a known identifier survived. Only stamped text may reach the external model route (see router).

Rule-based removal is deliberately the first layer; a model-based NER pass is added in Phase 2 for free-text
recall. HHS notes that properly de-identified data still carries some residual risk; so does this.
"""

from __future__ import annotations

import hashlib
import hmac
import re
from collections import Counter
from dataclasses import dataclass, field
from datetime import UTC, date, datetime

POLICY_VERSION = "safe-harbor/1"

MONTHS = (
    "January|February|March|April|May|June|July|August|September|October|November|December|"
    "Jan|Feb|Mar|Apr|Jun|Jul|Aug|Sep|Sept|Oct|Nov|Dec"
)

# Ordered: earlier patterns win over overlapping later ones.
PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    ("EMAIL", re.compile(r"\b[\w.+-]+@[\w-]+\.[\w.-]+\b")),
    ("URL", re.compile(r"\bhttps?://\S+|\bwww\.\S+", re.I)),
    ("IP", re.compile(r"\b(?:\d{1,3}\.){3}\d{1,3}\b")),
    ("SSN", re.compile(r"\b\d{3}-\d{2}-\d{4}\b")),
    ("PHONE", re.compile(r"(?:\+1[\s.-]?)?(?:\(\d{3}\)\s?|\b\d{3}[\s.-])\d{3}[\s.-]\d{4}\b")),
    ("RECORD_NUMBER", re.compile(
        r"\b(?:MRN|medical record(?: number)?|member(?: ID)?|account(?: number)?|policy(?: number)?|"
        r"subscriber(?: ID)?|license(?: number)?|serial(?: number)?)\s*(?:#|no\.?|number|ID)?\s*[:#]?\s*"
        r"[A-Z0-9][A-Z0-9-]{3,}\b", re.I)),
    ("DATE", re.compile(rf"\b(?:{MONTHS})\.?\s+\d{{1,2}}(?:st|nd|rd|th)?(?:,?\s+\d{{4}})?\b")),
    ("DATE", re.compile(r"\b\d{4}-\d{2}-\d{2}(?:T[\d:.]+(?:Z|[+-]\d{2}:\d{2})?)?\b")),
    ("DATE", re.compile(r"\b\d{1,2}/\d{1,2}/\d{2,4}\b")),
    ("ADDRESS", re.compile(
        r"\b\d{1,6}\s+(?:[A-Z][a-z]+\s){1,3}(?:Street|St|Avenue|Ave|Road|Rd|Lane|Ln|Drive|Dr|Way|Boulevard|Blvd|"
        r"Court|Ct|Place|Pl|Trail|Trl|Circle|Cir)\b\.?")),
    ("ZIP", re.compile(r"\b(?:[A-Z]{2}|New Mexico|Texas|Arizona|Colorado)\s+\d{5}(?:-\d{4})?\b")),
    ("AGE_OVER_89", re.compile(r"\b(?:9\d|1[0-2]\d)(?=[\s-]*(?:years?[\s-]old|yo\b|y/o))", re.I)),
]


@dataclass
class PatientContext:
    """Identifiers known for a case. Lives in the VPC only; never serialized into a model prompt."""

    names: list[str] = field(default_factory=list)
    birth_date: date | None = None
    address_lines: list[str] = field(default_factory=list)
    city: str | None = None
    postal_code: str | None = None
    phones: list[str] = field(default_factory=list)
    emails: list[str] = field(default_factory=list)
    record_numbers: list[str] = field(default_factory=list)  # MRN, member ID, account numbers, SSN

    def known_values(self) -> list[tuple[str, str]]:
        values: list[tuple[str, str]] = []
        for n in self.names:
            values.append(("NAME", n))
            values.extend(("NAME", part) for part in n.split() if len(part) > 2)
        if self.birth_date:
            d = self.birth_date
            values += [("DATE", d.isoformat()), ("DATE", f"{d:%B} {d.day}, {d.year}"),
                       ("DATE", f"{d.month}/{d.day}/{d.year}"), ("DATE", f"{d.month:02d}/{d.day:02d}/{d.year}")]
        values += [("ADDRESS", a) for a in self.address_lines]
        if self.city:
            values.append(("CITY", self.city))
        if self.postal_code:
            values.append(("ZIP", self.postal_code))
        values += [("PHONE", p) for p in self.phones] + [("EMAIL", e) for e in self.emails]
        values += [("RECORD_NUMBER", r) for r in self.record_numbers]
        # Longest first so "Harold Jenkins" is replaced before "Harold".
        return sorted({v for v in values if v[1]}, key=lambda v: -len(v[1]))


@dataclass
class DeidResult:
    text: str
    removed: Counter[str]
    token_map: dict[str, str]  # token -> original value. In-VPC only.


@dataclass(frozen=True)
class DeidStamp:
    policy: str
    text_sha256: str
    issued_at: str
    signature: str


@dataclass(frozen=True)
class StampedText:
    """De-identified text plus a stamp proving it passed this service unchanged."""

    text: str
    stamp: DeidStamp


class DeidentificationFailed(Exception):
    """A known identifier survived de-identification. Nothing is stamped."""


class SafeHarborDeidentifier:
    def __init__(self, signing_key: bytes):
        if len(signing_key) < 32:
            raise ValueError("the stamp signing key must be at least 32 bytes")
        self._key = signing_key

    def deidentify(self, text: str, ctx: PatientContext, tokens: dict[str, str] | None = None) -> DeidResult:
        token_map = dict(tokens or {})
        reverse = {v.lower(): k for k, v in token_map.items()}
        counters: Counter[str] = Counter(k.strip("[]").rsplit("_", 1)[0] for k in token_map)
        removed: Counter[str] = Counter()

        def token_for(category: str, value: str) -> str:
            key = value.lower()
            if key in reverse:
                return reverse[key]
            counters[category] += 1
            token = f"[{category}_{counters[category]}]"
            token_map[token] = value
            reverse[key] = token
            return token

        out = text
        for category, value in ctx.known_values():
            pattern = re.compile(rf"(?<![\w\[]){re.escape(value)}(?![\w\]])", re.I)
            out, n = pattern.subn(lambda m, c=category: token_for(c, m.group(0)), out)
            removed[category] += n

        for category, pattern in PATTERNS:
            def repl(m: re.Match[str], c: str = category) -> str:
                if c == "AGE_OVER_89":
                    removed[c] += 1
                    return "90 or older"
                return token_for(c, m.group(0))
            out, n = pattern.subn(repl, out)
            if category != "AGE_OVER_89":
                removed[category] += n

        residual = self.residual_identifiers(out, ctx)
        if residual:
            raise DeidentificationFailed(f"{len(residual)} known identifier(s) survived de-identification")
        return DeidResult(out, +removed, token_map)

    @staticmethod
    def residual_identifiers(text: str, ctx: PatientContext) -> list[str]:
        lowered = text.lower()
        hits = []
        for _, value in ctx.known_values():
            v = value.lower()
            if re.search(rf"(?<![\w\[]){re.escape(v)}(?![\w\]])", lowered):
                hits.append(value)
        return hits

    def stamp(self, result: DeidResult) -> StampedText:
        digest = hashlib.sha256(result.text.encode()).hexdigest()
        issued = datetime.now(UTC).isoformat(timespec="seconds")
        sig = hmac.new(self._key, f"{POLICY_VERSION}\n{digest}\n{issued}".encode(), hashlib.sha256).hexdigest()
        return StampedText(result.text, DeidStamp(POLICY_VERSION, digest, issued, sig))

    def verify(self, stamped: StampedText) -> bool:
        if not isinstance(stamped, StampedText) or not isinstance(stamped.stamp, DeidStamp):
            return False
        s = stamped.stamp
        if hashlib.sha256(stamped.text.encode()).hexdigest() != s.text_sha256:
            return False
        expected = hmac.new(self._key, f"{s.policy}\n{s.text_sha256}\n{s.issued_at}".encode(),
                            hashlib.sha256).hexdigest()
        return hmac.compare_digest(expected, s.signature)


def reidentify(text: str, token_map: dict[str, str]) -> str:
    """Put identifiers back into model output. Runs only inside the VPC."""
    for token, value in sorted(token_map.items(), key=lambda kv: -len(kv[0])):
        text = text.replace(token, value)
    return text
