"""The criteria DSL: a coverage policy compiled into a small, declarative, reviewable rule set.

An LLM may *propose* a criteria set from policy prose, but only a set a human reviewer has approved
(and whose content hash is in the lock file) is ever evaluated. See ADR 0002.
"""

from __future__ import annotations

import hashlib
import json
from datetime import date
from enum import StrEnum
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

SYSTEM_ALIASES: dict[str, str] = {
    "icd10": "http://hl7.org/fhir/sid/icd-10-cm",
    "snomed": "http://snomed.info/sct",
    "cpt": "http://www.ama-assn.org/go/cpt",
    "hcpcs": "https://www.cms.gov/Medicare/Coding/HCPCSReleaseCodeSets",
    "loinc": "http://loinc.org",
    "rxnorm": "http://www.nlm.nih.gov/research/umls/rxnorm",
    "local": "http://mesaverde.example/fhir/CodeSystem/caduceus",
}


def system_uri(alias_or_uri: str) -> str:
    return SYSTEM_ALIASES.get(alias_or_uri, alias_or_uri)


class Status(StrEnum):
    MET = "met"
    UNMET = "unmet"
    UNKNOWN = "unknown"


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class Code(_Strict):
    system: str
    code: str
    display: str | None = None

    @property
    def uri(self) -> str:
        return system_uri(self.system)


class ConditionRule(_Strict):
    """Matches an active Condition. Optional minimum duration from onset to the as-of date."""

    type: Literal["condition"]
    codes: list[Code]
    min_duration_days: int | None = None
    active_only: bool = True
    label: str = "diagnosis"


class ProcedureRule(_Strict):
    """Matches Procedures performed within a look-back window; optional span and count thresholds."""

    type: Literal["procedure"]
    codes: list[Code]
    within_days: int | None = None
    min_span_days: int | None = None
    min_count: int | None = None
    label: str = "procedure"


class ObservationRule(_Strict):
    """Matches Observations by code; numeric bounds (gte / lt) or a coded value."""

    type: Literal["observation"]
    codes: list[Code]
    within_days: int | None = None
    gte: float | None = None
    lt: float | None = None
    value_code: Code | None = None
    label: str = "finding"


class AnyOf(_Strict):
    type: Literal["any_of"]
    rules: list[Rule]


class AllOf(_Strict):
    type: Literal["all_of"]
    rules: list[Rule]


Rule = Annotated[ConditionRule | ProcedureRule | ObservationRule | AnyOf | AllOf, Field(discriminator="type")]
AnyOf.model_rebuild()
AllOf.model_rebuild()

CriterionKind = Literal["diagnosis", "prior_treatment", "observation", "time_window", "exclusion", "documentation"]


class Criterion(_Strict):
    id: str = Field(pattern=r"^[a-z][a-z0-9_]{1,63}$")
    text: str
    kind: CriterionKind
    policy_sentence: str
    rule: Rule
    # What absence of evidence means. "unknown" is the default and the only option outside exclusions:
    # the engine never guesses. A reviewer may accept absence as satisfying an exclusion, with a rationale.
    when_absent: Literal["unknown", "met"] = "unknown"
    absent_rationale: str | None = None
    # How the ordering clinician is asked, in plain words, when the record cannot answer this criterion.
    attestation_question: str | None = None
    attestation_help: str | None = None

    @model_validator(mode="after")
    def _absent_rules(self) -> Criterion:
        if self.when_absent == "met":
            if self.kind != "exclusion":
                raise ValueError(f"{self.id}: when_absent=met is only allowed for exclusion criteria")
            if not self.absent_rationale:
                raise ValueError(f"{self.id}: when_absent=met requires an absent_rationale from the reviewer")
        return self


class Source(_Strict):
    kind: Literal["mcd-mirror", "development-paraphrase"]
    title: str
    url: str | None = None
    note: str | None = None


class CriteriaSet(_Strict):
    id: str
    version: str
    title: str
    service_codes: list[Code]
    status: Literal["draft", "approved"]
    approved_by: str | None = None
    approved_at: date | None = None
    source: Source
    criteria: list[Criterion]

    @model_validator(mode="after")
    def _approval_fields(self) -> CriteriaSet:
        if self.status == "approved" and not (self.approved_by and self.approved_at):
            raise ValueError(f"{self.id}: an approved set needs approved_by and approved_at")
        ids = [c.id for c in self.criteria]
        if len(ids) != len(set(ids)):
            raise ValueError(f"{self.id}: criterion ids must be unique")
        return self

    @property
    def key(self) -> str:
        return f"{self.id}@{self.version}"

    def content_sha256(self) -> str:
        canonical = json.dumps(self.model_dump(mode="json"), sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(canonical.encode()).hexdigest()
