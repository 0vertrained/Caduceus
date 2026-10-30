"""Where patient records come from.

`LocalBundleStore` reads the trimmed Synthea bundles plus per-case overlays from disk (development and CI).
`HapiFhirStore` reads the same shapes from a HAPI FHIR JPA server (docker-compose and the deployed environment),
using `Patient/{id}/$everything`.
"""

from __future__ import annotations

import base64
import json
from pathlib import Path
from typing import Protocol

import httpx

from caduceus.fhir.record import PatientRecord


class FhirStore(Protocol):
    def record(self, patient_id: str, overlay_case: str | None = None) -> PatientRecord: ...
    def document(self, doc_id: str) -> dict | None: ...
    def find_patient(self, name: str, birth_date: str) -> str | None: ...


def document_text(doc: dict) -> str:
    for c in doc.get("content", []):
        att = c.get("attachment", {})
        if att.get("data"):
            return base64.b64decode(att["data"]).decode()
    return ""


class LocalBundleStore:
    def __init__(self, data_dir: Path):
        self._patients = data_dir / "fhir" / "patients"
        self._overlays = data_dir / "fhir" / "overlays"
        self._docs: dict[str, dict] = {}
        for f in sorted(self._overlays.glob("*.json")):
            for e in json.loads(f.read_text())["entry"]:
                r = e["resource"]
                if r["resourceType"] == "DocumentReference":
                    self._docs[r["id"]] = r

    def record(self, patient_id: str, overlay_case: str | None = None) -> PatientRecord:
        rec = PatientRecord.from_bundle(json.loads((self._patients / f"{patient_id}.json").read_text()))
        if overlay_case:
            overlay = json.loads((self._overlays / f"{overlay_case}.json").read_text())
            rec.extend(e["resource"] for e in overlay["entry"])
        return rec

    def document(self, doc_id: str) -> dict | None:
        return self._docs.get(doc_id)

    def find_patient(self, name: str, birth_date: str) -> str | None:
        """The one patient with this name and date of birth, or None. Used to match a faxed referral."""
        matches = []
        for f in sorted(self._patients.glob("*.json")):
            bundle = json.loads(f.read_text())
            p = next(e["resource"] for e in bundle["entry"] if e["resource"]["resourceType"] == "Patient")
            names = {" ".join([*n.get("given", [])[:1], n.get("family", "")]).lower() for n in p.get("name", [])}
            if name.strip().lower() in names and p.get("birthDate") == birth_date:
                matches.append(p["id"])
        return matches[0] if len(matches) == 1 else None


class HapiFhirStore:
    def __init__(self, base_url: str, client: httpx.Client | None = None):
        self._base = base_url.rstrip("/")
        self._client = client or httpx.Client(timeout=30)

    def record(self, patient_id: str, overlay_case: str | None = None) -> PatientRecord:
        entries, url = [], f"{self._base}/Patient/{patient_id}/$everything?_count=500"
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
