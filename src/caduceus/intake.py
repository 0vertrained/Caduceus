"""Reading faxed referrals, inside the boundary.

A fax is identifiable, so it is read where PHI may go: by Tesseract OCR running in this process, with no network.
Every field comes back with a confidence and the region of the page it came from. A field below its threshold, one
that fails validation, or a patient who cannot be matched in the record goes to a person, who confirms it against
the page before any case is created (ADR 6). Nothing downstream is built on an unconfirmed low-confidence field.
"""

from __future__ import annotations

import hashlib
import re
import shutil
import subprocess
import threading
from dataclasses import asdict, dataclass
from datetime import datetime
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any

THRESHOLD = 0.90  # below this a field is checked by a person; set from the faxes' evaluation (evals: fax_*)
LABELS = [("referral", "Referral number"), ("patient_name", "Patient name"), ("birth_date", "Date of birth"),
          ("member_id", "Member ID"), ("payer", "Health plan"), ("clinician", "Ordering clinician"),
          ("service_code", "CPT/HCPCS code"), ("service", "Requested service"), ("diagnosis", "Diagnosis")]
JUNK = " .,:;|\"'`“”‘’_-—~*"


class FaxReadingUnavailable(Exception):
    """OCR is not installed here. Faxes wait; nothing is guessed."""


@dataclass
class Field:
    key: str
    label: str
    value: str
    confidence: float
    box: tuple[int, int, int, int] | None  # left, top, width, height on the page, in pixels
    problem: str | None = None             # why a person should check it, in words

    @property
    def needs_check(self) -> bool:
        return self.problem is not None


_cache: dict[str, dict[str, Any]] = {}
_lock = threading.Lock()


def _letters(s: str) -> str:
    return re.sub(r"[^a-z0-9]", "", s.lower())


def ocr_words(path: Path) -> tuple[list[list[dict]], tuple[int, int]]:
    """Tesseract's words, grouped into lines, each with its confidence and box."""
    exe = shutil.which("tesseract")
    if exe is None:
        raise FaxReadingUnavailable("Tesseract is not installed")
    # Fixed arguments, no shell; the only input is the intake folder's own file, named by a validated id.
    out = subprocess.run([exe, str(path), "-", "--psm", "4", "tsv"],  # noqa: S603
                         capture_output=True, text=True, timeout=60, check=True).stdout
    lines: dict[tuple, list[dict]] = {}
    size = (0, 0)
    for row in out.splitlines()[1:]:
        c = row.split("\t")
        if len(c) < 12:
            continue
        if c[0] == "1":
            size = (int(c[8]), int(c[9]))
        if not c[11].strip() or float(c[10]) < 0:
            continue
        lines.setdefault((c[2], c[3], c[4]), []).append(
            {"text": c[11], "conf": float(c[10]) / 100, "box": (int(c[6]), int(c[7]), int(c[8]), int(c[9]))})
    return list(lines.values()), size


def _field(words: list[dict], label: str) -> tuple[str, float, tuple[int, int, int, int] | None, str] | None:
    """If this line starts with the label, the value after it: its text, lowest word confidence, box, and any junk
    that had to be stripped (a sign the reading is unsure)."""
    want, got, value_words, rest = _letters(label), "", [], ""
    for w in words:
        if len(got) < len(want):
            got += _letters(w["text"])
            if len(got) > len(want):  # the label ran into the value ("clinicianDr")
                rest, value_words = w["text"][-(len(got) - len(want)):], [w]
            continue
        value_words.append(w)
    if SequenceMatcher(None, got[:len(want)], want).ratio() < 0.85:
        return None
    raw = " ".join([rest] + [w["text"] for w in value_words if not (rest and w is value_words[0])]).strip()
    value = raw.strip(JUNK)
    value = re.sub(r"\s*[|“”]+\s*", " ", value).strip(JUNK)
    if not value_words:
        return "", 0.0, None, raw
    left = min(w["box"][0] for w in value_words)
    top = min(w["box"][1] for w in value_words)
    right = max(w["box"][0] + w["box"][2] for w in value_words)
    bottom = max(w["box"][1] + w["box"][3] for w in value_words)
    return value, min(w["conf"] for w in value_words), (left, top, right - left, bottom - top), raw


def read_fax(path: Path) -> dict[str, Any]:
    """Every labelled field on a referral cover sheet. Cached by the page's content: OCR is deterministic."""
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    with _lock:
        if digest in _cache:
            return _cache[digest]
    lines, size = ocr_words(path)
    fields: dict[str, Field] = {}
    for key, label in LABELS:
        for words in lines:
            found = _field(words, label)
            if found is not None and key not in fields:
                value, conf, box, raw = found
                junk = value != raw.strip()
                fields[key] = Field(key, label, value, round(conf * (0.85 if junk else 1), 2), box)
        fields.setdefault(key, Field(key, label, "", 0.0, None, "This line could not be found on the page."))
    result = {"sha256": digest, "size": size, "fields": [asdict(f) for f in fields.values()],
              "read_at": datetime.now().astimezone().isoformat(timespec="seconds"), "engine": "Tesseract OCR"}
    with _lock:
        _cache[digest] = result
    return result


def check(fields: list[dict], known: dict[str, set[str]]) -> list[dict]:
    """Mark each field a person must confirm, and say why. `known` holds the valid payers, clinicians and codes."""
    rules = {
        "referral": (lambda v: re.fullmatch(r"\d{4}-\d{4}", v), "A referral number looks like 2026-1029."),
        "birth_date": (lambda v: _date(v), "A date of birth is written MM/DD/YYYY."),
        "member_id": (lambda v: re.fullmatch(r"[A-Z]{2}\d{9}", v), "A member ID is two letters and nine digits."),
        "payer": (lambda v: v in known["payers"], "This is not a health plan Mesa Verde works with."),
        "clinician": (lambda v: v in known["clinicians"], "This is not an ordering clinician on file."),
        "service_code": (lambda v: v in known["codes"], "This is not a service code this team handles."),
        "diagnosis": (lambda v: re.match(r"[A-Z]\d{2}\.\d+ \S", v), "A diagnosis starts with its ICD-10 code."),
    }
    out = []
    for f in fields:
        f = dict(f) | {"problem": f.get("problem")}
        valid, why = rules.get(f["key"], (lambda v: bool(v), "This field is empty."))
        if not f.get("problem"):
            if not f["value"]:
                f["problem"] = "Nothing could be read here."
            elif not valid(f["value"]):
                f["problem"] = why
            elif f["confidence"] < THRESHOLD:
                f["problem"] = f"Read with {round(f['confidence'] * 100)}% confidence; below {round(THRESHOLD * 100)}%."
        out.append(f)
    return out


def _date(v: str) -> str | None:
    try:
        return datetime.strptime(v, "%m/%d/%Y").date().isoformat()
    except ValueError:
        return None


def iso_date(v: str) -> str | None:
    return _date(v)
