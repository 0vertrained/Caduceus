"""Where patient records come from: bundles and per-case overlays on disk, or a HAPI FHIR server, plus the documents
Caduceus files itself and the records an EHR shares by SMART launch."""

from __future__ import annotations

import base64
import json
import re
from collections.abc import Iterable
from difflib import SequenceMatcher
from pathlib import Path
from typing import Protocol

import httpx

from caduceus.fhir.record import PatientRecord


class FhirStore(Protocol):
    def record(self, patient_id: str, overlay_case: str | None = None) -> PatientRecord: ...
    def document(self, doc_id: str) -> dict | None: ...
    def find_patient(self, name: str, birth_date: str) -> str | None: ...
    def candidates(self, name: str, birth_date: str, limit: int = 3) -> list[dict]: ...


def name_variants(patient: dict) -> set[str]:
    """Every name the record knows the patient by (official, maiden), as "first last" in lower case."""
    return {" ".join([*n.get("given", [])[:1], n.get("family", "")]).lower() for n in patient.get("name", [])}


def rank(name: str, birth_date: str, patients: Iterable[dict], limit: int = 3) -> list[dict]:
    """The patients most like this name, best first; the same birth year counts for a little, the same date for
    more. Only for a person to choose from: a case is created only when the name and date match a record exactly."""
    wanted = " ".join(name.lower().split())
    scored = []
    for p in patients:
        alike = max((SequenceMatcher(None, wanted, v).ratio() for v in name_variants(p)), default=0.0)
        born = p.get("birthDate", "")
        same_year = bool(birth_date[:4]) and born[:4] == birth_date[:4]
        bonus = 0.3 if birth_date and born == birth_date else 0.1 if same_year else 0.0
        if alike >= 0.5 and alike + bonus >= 0.75:
            scored.append((alike + bonus, p["id"], p))
    return [p for _, _, p in sorted(scored, key=lambda s: (-s[0], s[1]))[:limit]]


def document_text(doc: dict) -> str:
    for c in doc.get("content", []):
        att = c.get("attachment", {})
        if att.get("data"):
            return base64.b64decode(att["data"]).decode()
    return ""


class LocalBundleStore:
    """Bundles on disk, from one data directory or several (the demo adds data/showcase to data). Each directory
    holds fhir/patients/<patient id>.json and fhir/overlays/<case id>.json; an id is looked up in each in turn."""

    def __init__(self, data_dir: Path, *more: Path):
        self._roots = [d / "fhir" for d in (data_dir, *more)]
        self._docs: dict[str, dict] = {}
        self._index: list[dict] | None = None
        for root in self._roots:
            for f in sorted((root / "overlays").glob("*.json")):
                for e in json.loads(f.read_text())["entry"]:
                    r = e["resource"]
                    if r["resourceType"] == "DocumentReference":
                        self._docs[r["id"]] = r

    def _file(self, kind: str, name: str) -> Path:
        """The first directory's file of that name, or the first directory's path, so a missing one says where."""
        paths = [root / kind / f"{name}.json" for root in self._roots]
        return next((p for p in paths if p.exists()), paths[0])

    def record(self, patient_id: str, overlay_case: str | None = None) -> PatientRecord:
        rec = PatientRecord.from_bundle(json.loads(self._file("patients", patient_id).read_text()))
        if overlay_case:
            overlay = json.loads(self._file("overlays", overlay_case).read_text())
            rec.extend(e["resource"] for e in overlay["entry"])
        return rec

    def document(self, doc_id: str) -> dict | None:
        return self._docs.get(doc_id)

    def _everyone(self) -> list[dict]:
        """Each patient's Patient resource, read once: a bundle holds the whole record, and a search needs only this."""
        if self._index is None:
            self._index = []
            for f in [f for root in self._roots for f in sorted((root / "patients").glob("*.json"))]:
                bundle = json.loads(f.read_text())
                self._index.append(next(e["resource"] for e in bundle["entry"]
                                        if e["resource"]["resourceType"] == "Patient"))
        return self._index

    def find_patient(self, name: str, birth_date: str) -> str | None:
        """The one patient with this name and date of birth, or None. Used to match a faxed referral."""
        wanted = " ".join(name.lower().split())
        matches = [p["id"] for p in self._everyone() if wanted in name_variants(p) and p.get("birthDate") == birth_date]
        return matches[0] if len(matches) == 1 else None

    def candidates(self, name: str, birth_date: str, limit: int = 3) -> list[dict]:
        return rank(name, birth_date, self._everyone(), limit)


class Filed(Protocol):
    def get(self, doc_id: str) -> dict | None: ...


class WithFiledDocuments:
    """The record's store, plus the documents Caduceus filed itself (a fax cover sheet), found by the same id lookup."""

    def __init__(self, base: FhirStore, filed: Filed):
        self.base, self.filed = base, filed

    def record(self, patient_id: str, overlay_case: str | None = None) -> PatientRecord:
        return self.base.record(patient_id, overlay_case)

    def document(self, doc_id: str) -> dict | None:
        return self.filed.get(doc_id) or self.base.document(doc_id)

    def find_patient(self, name: str, birth_date: str) -> str | None:
        return self.base.find_patient(name, birth_date)

    def candidates(self, name: str, birth_date: str, limit: int = 3) -> list[dict]:
        return self.base.candidates(name, birth_date, limit)


class Kept(Protocol):
    def get(self, key: str) -> dict | None: ...


EHR_PAGE = re.compile(r"(ehr-[0-9a-f]{20})-(\d{1,2})")


class WithEhrRecords:
    """The store, plus records read from an EHR by a SMART launch, named by their key, with their newest notes as the
    case's pages. A fax is never matched to them: a faxed referral is checked against the clinic's own chart."""

    def __init__(self, base: FhirStore, kept: Kept):
        self.base, self.kept = base, kept

    def record(self, patient_id: str, overlay_case: str | None = None) -> PatientRecord:
        if patient_id.startswith("ehr-"):
            data = self.kept.get(patient_id)
            if data is None:
                raise KeyError(patient_id)
            return PatientRecord(patient=data["patient"], resources=list(data["resources"]))
        return self.base.record(patient_id, overlay_case)

    def document(self, doc_id: str) -> dict | None:
        m = EHR_PAGE.fullmatch(doc_id)
        if m:
            notes = (self.kept.get(m.group(1)) or {}).get("notes", [])
            n = int(m.group(2))
            return notes[n - 1] | {"id": doc_id} if 0 < n <= len(notes) else None
        return self.base.document(doc_id)

    def find_patient(self, name: str, birth_date: str) -> str | None:
        return self.base.find_patient(name, birth_date)

    def candidates(self, name: str, birth_date: str, limit: int = 3) -> list[dict]:
        return self.base.candidates(name, birth_date, limit)


def fax_image(doc: dict) -> str | None:
    """The fax page image a filed document came from, if it did: the intake folder's own file name."""
    for c in doc.get("content", []):
        url = c.get("attachment", {}).get("url", "")
        if url.startswith("fax:"):
            return url.removeprefix("fax:")
    return None


class HapiFhirStore:
    def __init__(self, base_url: str, client: httpx.Client | None = None):
        self._base = base_url.rstrip("/")
        self._client = client or httpx.Client(timeout=30)

    def record(self, patient_id: str, overlay_case: str | None = None) -> PatientRecord:
        entries: list[dict] = []
        url: str | None = f"{self._base}/Patient/{patient_id}/$everything?_count=500"
        while url:
            page = self._client.get(url).raise_for_status().json()
            entries += page.get("entry", [])
            url = next((link["url"] for link in page.get("link", []) if link.get("relation") == "next"), None)
        return PatientRecord.from_bundle({"entry": entries})

    def document(self, doc_id: str) -> dict | None:
        r = self._client.get(f"{self._base}/DocumentReference/{doc_id}")
        return r.json() if r.status_code == 200 else None

    def find_patient(self, name: str, birth_date: str) -> str | None:
        r = self._client.get(f"{self._base}/Patient", params={"name": name, "birthdate": birth_date})
        r.raise_for_status()
        entries = r.json().get("entry", [])
        return entries[0]["resource"]["id"] if len(entries) == 1 else None

    def candidates(self, name: str, birth_date: str, limit: int = 3) -> list[dict]:
        """Patients whose family name starts like this one's, or who share the date of birth, ranked here. The search
        stays inside the boundary, between this service and the FHIR server."""
        found: dict[str, dict] = {}
        family = name.split()[-1][:4] if name.split() else ""
        for params in ([{"family": family}] if family else []) + ([{"birthdate": birth_date}] if birth_date else []):
            r = self._client.get(f"{self._base}/Patient", params=params | {"_count": "50"})
            r.raise_for_status()
            found |= {e["resource"]["id"]: e["resource"] for e in r.json().get("entry", [])}
        return rank(name, birth_date, found.values(), limit)
