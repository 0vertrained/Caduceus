"""Reading faxed referrals, inside the boundary.

A fax is identifiable, so it is read where PHI may go: by Tesseract OCR running in this process, with no network.
Every field comes back with a confidence and the region of the page it came from. A field below its threshold, one
that fails validation, or a patient who cannot be matched in the record goes to a person, who confirms it against
the page before any case is created (ADR 6). Nothing downstream is built on an unconfirmed low-confidence field.
"""

from __future__ import annotations

import base64
import hashlib
import re
import shutil
import subprocess
import threading
from collections.abc import Iterable
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
# The form a person checks, in the order the cover sheet gives it. The service's code and its name are two lines on
# the page but one choice on the form: a person picks the service, and its code and name come from the catalog.
FORM = ["referral", "patient_name", "birth_date", "member_id", "payer", "clinician", "service_code", "diagnosis"]
SUGGEST_FLOOR = 0.6  # how like what was read the closest option must be before the form starts on it


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
    return value, min(w["conf"] for w in value_words), _box([w["box"] for w in value_words]), raw


def _box(boxes: Iterable[tuple[int, int, int, int] | None]) -> tuple[int, int, int, int] | None:
    """The smallest region holding all these regions."""
    found = [b for b in boxes if b]
    if not found:
        return None
    left, top = min(b[0] for b in found), min(b[1] for b in found)
    right, bottom = max(b[0] + b[2] for b in found), max(b[1] + b[3] for b in found)
    return left, top, right - left, bottom - top


def _sender(lines: list[list[dict]]) -> dict[str, Any]:
    """The cover sheet's "From:" line: the clinic that sent it. The clinic's fax number follows it and is left out."""
    for words in lines:
        if not words or not _letters(words[0]["text"]).startswith("from"):
            continue
        name = []
        for w in words[1:]:
            if _letters(w["text"]).startswith("fax"):
                break
            name.append(w)
        text = re.sub(r"\s*[|“”]+\s*", " ", " ".join(w["text"] for w in name)).strip(JUNK)
        if text:
            return {"value": text, "confidence": round(min(w["conf"] for w in name), 2),
                    "box": _box(w["box"] for w in name)}
    return {"value": "", "confidence": 0.0, "box": None}


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
              "sender": _sender(lines), "text": "\n".join(" ".join(w["text"] for w in words) for words in lines),
              "read_at":datetime.now().astimezone().isoformat(timespec="seconds"), "engine": "Tesseract OCR"}
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


def _ratio(a: str, b: str) -> float:
    return SequenceMatcher(None, _letters(a), _letters(b)).ratio()


def closest(text: str, options: Iterable[str], floor: float = SUGGEST_FLOOR) -> str | None:
    """The option most like what was read, if any is like it at all. Only ever where the form starts: a field that was
    not read clearly still needs a person to tick it, whatever it starts on."""
    options = list(options)
    if not _letters(text) or not options:
        return None
    best = max(options, key=lambda o: (_ratio(text, o), o))
    return best if _ratio(text, best) >= floor else None


def closest_service(code_text: str, name_text: str, services: dict[str, dict]) -> str | None:
    """The service code the fax most likely gives: a known code written out ("72148 one"), else a known code read a
    little wrong ("72I48"), else the service named on the next line."""
    for token in re.findall(r"\b(?:[A-Z]\d{4}|\d{5})\b", code_text.upper()):
        if token in services:
            return token
    by_code = closest(code_text, services, floor=0.8)
    if by_code:
        return by_code
    names = {s["display"]: code for code, s in services.items()}
    by_name = closest(name_text, names, floor=0.75)
    return names[by_name] if by_name else None


def date_value(v: str) -> str:
    """A date read as three numbers, written MM/DD/YYYY for the form's three boxes; empty if it is not three."""
    parts = re.findall(r"\d+", v)
    if len(parts) == 3 and len(parts[2]) == 4 and all(len(p) <= 2 for p in parts[:2]):
        return f"{int(parts[0]):02d}/{int(parts[1]):02d}/{parts[2]}"
    return ""


def service_label(service: dict) -> str:
    return f"{service['display']} ({service['system'].upper()} {service['code']})"


def side_field(diagnosis: dict[str, Any], for_services: list[str]) -> dict[str, Any]:
    """Which side a sided request is for (a knee replacement is for one knee). The fax has no box for it, so it is
    read from the diagnosis ("M17.11 Primary osteoarthritis, right knee"); when the diagnosis names no side, a person
    must choose one. The field counts only when the chosen service needs a side."""
    read = diagnosis.get("value", "")
    found = re.search(r"\b(right|left)\b", read, re.IGNORECASE)
    return {"key": "side", "label": "Side", "kind": "select", "read_as": read,
            "initial": found.group(1).lower() if found else "",
            "problem": None if found else "The diagnosis does not say which side.",
            "confidence": diagnosis.get("confidence", 0.0), "box": diagnosis.get("box"),
            "options": [{"value": "right", "label": "Right"}, {"value": "left", "label": "Left"}],
            "for_services": for_services}


def form(reading: dict[str, Any], catalog: dict[str, Any]) -> dict[str, Any]:
    """What a person checks, in the order the cover sheet gives it: each field with what was read, any reason to check
    it, and the value the form starts with. The health plan, the clinician and the service are chosen from the ones on
    file, starting on the one closest to what was read; clinicians are narrowed to the clinic the fax came from.

    The starting values matter to the rule a person follows: a field sent to a person counts as checked only when they
    tick it, or change it from where the form started (see `unchecked`)."""
    by = {f["key"]: f for f in reading.get("fields", [])}
    sender = reading.get("sender") or {"value": "", "box": None}
    clinics: dict[str, str] = catalog.get("clinics", {})  # clinician's name -> their clinic
    clinic = closest(sender["value"], sorted(set(clinics.values())), floor=0.8)
    fields = []
    for key in FORM:
        f = by[key]
        item = {"key": key, "label": f["label"], "kind": "text", "read_as": f["value"], "initial": f["value"],
                "problem": f.get("problem"), "confidence": f["confidence"], "box": f["box"]}
        if key == "birth_date":
            item |= {"kind": "date", "initial": date_value(f["value"])}
        elif key == "payer":
            item |= {"kind": "select", "initial": closest(f["value"], sorted(catalog["payers"])) or ""}
        elif key == "clinician":
            here = sorted(name for name, c in clinics.items() if c == clinic)
            pick = closest(f["value"], here) or closest(f["value"], sorted(catalog["clinicians"]))
            item |= {"kind": "select", "initial": pick or "", "at_clinic": here}
        elif key == "service_code":
            name = by["service"]
            item |= {"label": "Requested service", "kind": "select",
                     "read_as": ", ".join(v for v in (f["value"], name["value"]) if v),
                     "initial": closest_service(f["value"], name["value"], catalog["services"]) or "",
                     "problem": f.get("problem") or name.get("problem"),
                     "confidence": min(f["confidence"], name["confidence"]), "box": _box([f["box"], name["box"]])}
        fields.append(item)
        if key == "service_code":
            sided = sorted(code for code, s in catalog["services"].items() if s.get("needs_side"))
            if sided:
                fields.append(side_field(by.get("diagnosis") or {}, sided))
    return {"fields": fields, "clinic": clinic, "sender": {"read_as": sender["value"], "box": sender["box"]},
            "choices": {
                "payers": sorted(catalog["payers"]),
                "clinicians": [{"name": n, "specialty": c.get("specialty", ""), "clinic": clinics.get(n)}
                               for n, c in sorted(catalog["clinicians"].items())],
                "services": [{"code": code, "label": service_label(s)}
                             for code, s in sorted(catalog["services"].items(), key=lambda kv: kv[1]["display"])]}}


# What a request to the clinic calls each field it asks for.
ASKED_FOR = {"referral": "the referral number", "patient_name": "the patient’s name",
             "birth_date": "the patient’s date of birth", "member_id": "the member ID", "payer": "the health plan",
             "clinician": "the ordering clinician", "service_code": "the requested service and its code",
             "diagnosis": "the diagnosis and its ICD-10 code"}


# What a sentence calls each field ("corrected the member ID and the ordering clinician").
FIELD_WORDS = {"referral": "the referral number", "patient_name": "the patient’s name",
               "birth_date": "the date of birth", "member_id": "the member ID", "payer": "the health plan",
               "clinician": "the ordering clinician",
               "service_code": "the requested service", "service": "the requested service",
               "diagnosis": "the diagnosis"}


def listed(items: list[str]) -> str:
    return items[0] if len(items) == 1 else f"{', '.join(items[:-1])} and {items[-1]}"


def fields_in_words(keys: Iterable[str]) -> str:
    """The fields, in the order the cover sheet gives them, as a sentence names them; empty if there are none."""
    named = list(dict.fromkeys(FIELD_WORDS[k] for k, _ in LABELS if k in set(keys)))
    return listed(named) if named else ""


def cover_sheet(doc_id: str, fax_file: str, reading: dict[str, Any], received_at: str, clinic: str | None) -> dict:
    """The faxed cover sheet as a document for the case's packet: the text read from it, and where its image is. Shaped
    like the record's DocumentReferences, so every tool and screen that reads a packet page reads this one too."""
    text = reading.get("text") or "\n".join(f"{f['label']}: {f['value']}" for f in reading.get("fields", []))
    return {"resourceType": "DocumentReference", "id": doc_id, "status": "current", "description": "Fax cover sheet",
            "date": received_at, "author": [{"display": clinic}] if clinic else [],
            "content": [{"attachment": {"contentType": "text/plain", "title": "Read by OCR",
                                        "data": base64.b64encode(text.encode()).decode()}},
                        {"attachment": {"contentType": "image/png", "title": "The faxed page",
                                        "url": f"fax:{fax_file}"}}]}


def clinic_request(clinic: str | None, referral: str | None, received: str, missing: list[str], resend: bool) -> str:
    """The request a person sends the clinic, in plain words: what arrived, what is missing, what to do. It names the
    referral and the fields, never the patient, so it can go by fax or phone without more care than the referral."""
    which = f"referral {referral}" if referral else "a prior authorization referral"
    sent = f"We received {which} from you by fax on {received}"
    if resend:
        body = f"{sent}, but our copy is too poor to read. Please fax the whole referral again, with its notes."
    else:
        them = "it" if len(missing) == 1 else "them"
        body = (f"{sent}, but the cover sheet does not clearly give {listed([ASKED_FOR[k] for k in missing])}. "
                f"Please fax the cover sheet again with {them} filled in, or call us with {them}.")
    return (f"To {clinic or 'the referring clinic'}:\n\n{body} We will start the request as soon as we have "
            f"{'it' if resend else them}.\n\nThank you,\nPrior Authorization Team, Mesa Verde Health")


def unchecked(fields: list[dict], values: dict[str, str], ticked: Iterable[str]) -> list[dict]:
    """Fields sent to a person that they neither ticked as matching the fax nor changed from where the form began."""
    ticked = set(ticked)
    return [f for f in fields if f["problem"] and f["key"] not in ticked and values.get(f["key"], "") == f["initial"]]


def _date(v: str) -> str | None:
    try:
        return datetime.strptime(v, "%m/%d/%Y").date().isoformat()
    except ValueError:
        return None


def iso_date(v: str) -> str | None:
    return _date(v)
