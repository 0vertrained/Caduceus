"""A read-only view of one patient's FHIR R4 record, with the date and code helpers the engine needs.

The engine never sees a FHIR server or an HTTP client: it receives a PatientRecord built from whatever
store is configured (local bundle files in development, HAPI FHIR in the deployed environment).
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Iterator
from dataclasses import dataclass, field
from datetime import date, datetime

SOURCE_EXT = "http://mesaverde.example/fhir/StructureDefinition/source-sentence"


def parse_date(value: str | None) -> date | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).date()
    except ValueError:
        return date.fromisoformat(value[:10])


def codings(resource: dict, element: str = "code") -> list[tuple[str, str]]:
    concept = resource.get(element) or {}
    return [(c.get("system", ""), c.get("code", "")) for c in concept.get("coding", [])]


SEMANTIC_TAG = re.compile(r"\s*\((?:disorder|finding|procedure|situation|observable entity|regime/therapy)\)$")


def display(resource: dict, element: str = "code") -> str:
    """Human display for a coded element, without SNOMED semantic tags such as "(disorder)"."""
    concept = resource.get(element) or {}
    text = concept.get("text") or next((c["display"] for c in concept.get("coding", []) if c.get("display")), None)
    return SEMANTIC_TAG.sub("", text) if text else resource.get("resourceType", "Resource")


def source_of(resource: dict) -> tuple[str | None, str | None]:
    """The note and sentence a structured resource was derived from, when intake recorded it."""
    for ext in resource.get("extension", []):
        if ext.get("url") == SOURCE_EXT:
            parts = {e["url"]: e.get("valueString") or e.get("valueReference", {}).get("reference")
                     for e in ext.get("extension", [])}
            return parts.get("document"), parts.get("sentence")
    return None, None


@dataclass
class PatientRecord:
    patient: dict
    resources: list[dict] = field(default_factory=list)

    @property
    def id(self) -> str:
        return self.patient["id"]

    def of_type(self, resource_type: str) -> Iterator[dict]:
        return (r for r in self.resources if r.get("resourceType") == resource_type)

    def get(self, resource_type: str, resource_id: str) -> dict | None:
        for r in self.of_type(resource_type):
            if r.get("id") == resource_id:
                return r
        return None

    @classmethod
    def from_bundle(cls, bundle: dict) -> PatientRecord:
        resources = [e["resource"] for e in bundle.get("entry", [])]
        patients = [r for r in resources if r.get("resourceType") == "Patient"]
        if len(patients) != 1:
            raise ValueError(f"expected exactly one Patient in bundle, found {len(patients)}")
        return cls(patient=patients[0], resources=[r for r in resources if r is not patients[0]])

    def extend(self, resources: Iterable[dict]) -> None:
        self.resources.extend(resources)


# Date accessors per resource type -----------------------------------------------------------------


def condition_onset(r: dict) -> date | None:
    return parse_date(r.get("onsetDateTime") or (r.get("onsetPeriod") or {}).get("start") or r.get("recordedDate"))


VOIDED = {"refuted", "entered-in-error"}


def condition_is_voided(r: dict) -> bool:
    """Ruled out, or charted in error: such a diagnosis is not the patient's, whatever its clinical status says."""
    return bool({c.get("code") for c in (r.get("verificationStatus") or {}).get("coding", [])} & VOIDED)


def condition_onset_is_known(r: dict) -> bool:
    """True when the record says when the problem began; recordedDate alone says only when it was charted."""
    return bool(r.get("onsetDateTime") or (r.get("onsetPeriod") or {}).get("start"))


def condition_is_active(r: dict, as_of: date) -> bool:
    if condition_is_voided(r):
        return False
    status = {c.get("code") for c in (r.get("clinicalStatus") or {}).get("coding", [])}
    if status and not status & {"active", "recurrence", "relapse"}:
        return False
    abated = parse_date(r.get("abatementDateTime"))
    return abated is None or abated > as_of


def procedure_period(r: dict) -> tuple[date | None, date | None]:
    if "performedPeriod" in r:
        p = r["performedPeriod"]
        start, end = parse_date(p.get("start")), parse_date(p.get("end"))
        return start, end or start
    d = parse_date(r.get("performedDateTime"))
    return d, d


def observation_date(r: dict) -> date | None:
    return parse_date(r.get("effectiveDateTime") or (r.get("effectivePeriod") or {}).get("start") or r.get("issued"))
