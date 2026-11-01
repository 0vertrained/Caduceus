"""The deterministic criteria engine: met, unmet or unknown for each criterion of an approved set, with the evidence
and an explanation in words. No model is called here. When the record holds no matching evidence, the answer is
unknown, never a guess.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import date

from caduceus.criteria.dsl import (
    AgeRule,
    AllOf,
    AnyOf,
    Code,
    ConditionRule,
    CriteriaSet,
    Criterion,
    ObservationRule,
    ProcedureRule,
    Status,
)
from caduceus.fhir.record import (
    PatientRecord,
    codings,
    condition_is_active,
    condition_is_voided,
    condition_onset,
    condition_onset_is_known,
    display,
    observation_date,
    procedure_period,
    source_of,
)


@dataclass(frozen=True)
class Evidence:
    resource_type: str
    resource_id: str
    display: str
    date: str | None
    source_document: str | None = None
    source_sentence: str | None = None


@dataclass
class RuleOutcome:
    status: Status
    detail: str
    evidence: list[Evidence] = field(default_factory=list)


@dataclass
class CriterionResult:
    criterion_id: str
    text: str
    kind: str
    status: Status
    explanation: str
    policy_sentence: str
    evidence: list[Evidence]

    def to_dict(self) -> dict:
        d = asdict(self)
        d["status"] = self.status.value
        return d


@dataclass
class Determination:
    criteria_set: str
    content_sha256: str
    as_of: str
    results: list[CriterionResult]

    @property
    def counts(self) -> dict[str, int]:
        out = {s.value: 0 for s in Status}
        for r in self.results:
            out[r.status.value] += 1
        return out

    @property
    def overall(self) -> str:
        statuses = {r.status for r in self.results}
        if statuses == {Status.MET}:
            return "all_met"
        if Status.UNMET in statuses:
            return "not_met"
        return "needs_attestation"

    def to_dict(self) -> dict:
        return {
            "criteria_set": self.criteria_set,
            "content_sha256": self.content_sha256,
            "as_of": self.as_of,
            "overall": self.overall,
            "counts": self.counts,
            "results": [r.to_dict() for r in self.results],
        }


def _span(days: int) -> str:
    """Durations in the words a person would use: days, then weeks, then months, then years."""
    if days >= 730:
        return f"{days // 365} years"
    if days >= 91:
        return f"{round(days / 30.44)} months"
    if days >= 14:
        return f"{days // 7} weeks"
    return f"{days} day" + ("" if days == 1 else "s")


def _cap(s: str) -> str:
    return s[:1].upper() + s[1:]


def _when(d: date) -> str:
    return f"{d:%B} {d.day}, {d.year}"


def _matches(resource: dict, codes: list[Code], element: str = "code") -> bool:
    wanted = {(c.uri, c.code) for c in codes}
    return any(pair in wanted for pair in codings(resource, element))


def _evidence(resource: dict, when: date | None) -> Evidence:
    doc, sentence = source_of(resource)
    return Evidence(
        resource_type=resource["resourceType"],
        resource_id=resource["id"],
        display=display(resource),
        date=when.isoformat() if when else None,
        source_document=doc,
        source_sentence=sentence,
    )


class Engine:
    def __init__(self, approved: dict[str, str]):
        """`approved` maps criteria-set key (id@version) to the sha256 a reviewer approved."""
        self._approved = approved

    @property
    def approved(self) -> dict[str, str]:
        """The approved hash of each criteria set, by key (a copy)."""
        return dict(self._approved)

    def evaluate(self, criteria_set: CriteriaSet, record: PatientRecord, as_of: date,
                 side: str | None = None) -> Determination:
        """`side` is the side the request names ("right" or "left"), for criteria that must match it."""
        digest = criteria_set.content_sha256()
        if criteria_set.status != "approved":
            raise UnapprovedCriteriaSet(f"{criteria_set.key} is a draft; only approved sets are evaluated")
        if self._approved.get(criteria_set.key) != digest:
            raise UnapprovedCriteriaSet(
                f"{criteria_set.key} does not match its approved content hash; it was changed after approval"
            )
        results = [self._criterion(c, record, as_of, side) for c in criteria_set.criteria]
        return Determination(criteria_set.key, digest, as_of.isoformat(), results)

    # Criteria: exclusions invert, and when_absent applies ----------------------------------------------------

    def _criterion(self, c: Criterion, record: PatientRecord, as_of: date, side: str | None) -> CriterionResult:
        outcome = self._rule(c.rule, record, as_of, side)
        status, explanation = outcome.status, outcome.detail
        if c.kind == "exclusion":
            if outcome.status == Status.MET:
                status, explanation = Status.UNMET, f"Excluding finding present: {outcome.detail}"
            elif c.when_absent == "met":
                status = Status.MET
                explanation = f"No excluding finding in the record. Reviewer rule: {c.absent_rationale}"
            else:
                status = Status.UNKNOWN
                explanation = "No excluding finding in the record, but absence is not accepted as proof."
        return CriterionResult(c.id, c.text, c.kind, status, explanation, c.policy_sentence, outcome.evidence)

    # Rules ---------------------------------------------------------------------------------------------------

    def _rule(self, rule, record: PatientRecord, as_of: date, side: str | None = None) -> RuleOutcome:
        if isinstance(rule, ConditionRule):
            return self._condition(rule, record, as_of, side)
        if isinstance(rule, ProcedureRule):
            return self._procedure(rule, record, as_of)
        if isinstance(rule, ObservationRule):
            return self._observation(rule, record, as_of)
        if isinstance(rule, AgeRule):
            return self._age(rule, record, as_of)
        if isinstance(rule, AnyOf):
            return self._any_of(rule, record, as_of, side)
        if isinstance(rule, AllOf):
            return self._all_of(rule, record, as_of, side)
        raise TypeError(f"unsupported rule {type(rule).__name__}")

    def _condition(self, rule: ConditionRule, record: PatientRecord, as_of: date,
                   side: str | None = None) -> RuleOutcome:
        found = []
        for r in record.of_type("Condition"):
            onset = condition_onset(r)
            if not _matches(r, rule.codes) or (onset and onset > as_of) or condition_is_voided(r):
                continue
            if rule.active_only and not condition_is_active(r, as_of):
                continue
            found.append((onset, r))
        if not found:
            return RuleOutcome(Status.UNKNOWN, f"No matching {rule.label} was found in the record.")
        if rule.same_side:
            sided = self._same_side(rule, found, side)
            if isinstance(sided, RuleOutcome):
                return sided
            found = sided
        found.sort(key=lambda t: t[0] or as_of)
        onset, earliest = found[0]
        evidence = [_evidence(r, d) for d, r in found]
        if rule.min_duration_days is None:
            return RuleOutcome(Status.MET, f"{display(earliest)} is on the record.", evidence)
        if onset is None:
            return RuleOutcome(Status.UNKNOWN, f"{display(earliest)} is recorded without a start date.", evidence)
        days = (as_of - onset).days
        if not condition_onset_is_known(earliest):
            # Only the charting date is known: the problem has lasted at least this long, perhaps much longer.
            if days >= rule.min_duration_days:
                return RuleOutcome(Status.MET, f"Charted {_span(days)} ago, on {_when(onset)}, with no onset date; "
                                   f"it has lasted at least that long.", evidence)
            return RuleOutcome(Status.UNKNOWN, f"Charted {_span(days)} ago with no onset date, so the record cannot "
                               f"say whether it has lasted {_span(rule.min_duration_days)}.", evidence)
        if days >= rule.min_duration_days:
            return RuleOutcome(Status.MET, f"Documented for {_span(days)}, since {_when(onset)}.", evidence)
        return RuleOutcome(
            Status.UNMET,
            f"Documented for {_span(days)}; the policy requires at least {_span(rule.min_duration_days)}.",
            evidence,
        )

    @staticmethod
    def _same_side(rule: ConditionRule, found: list, side: str | None) -> list | RuleOutcome:
        """Keep the diagnoses on the requested side. A diagnosis charted only on the other side is not met: it
        may be a wrong-side request. One charted without a side cannot answer, so the clinician is asked."""
        sides = {(c.uri, c.code): c.side for c in rule.codes}
        def side_of(r: dict) -> str | None:
            return next((sides[p] for p in codings(r) if p in sides and sides[p]), None)
        if side is None:
            return RuleOutcome(Status.UNKNOWN, f"The request does not say which side, so the {rule.label} "
                               "cannot be matched to it.", [_evidence(r, d) for d, r in found])
        same = [(d, r) for d, r in found if side_of(r) in (side, "bilateral")]
        if same:
            return same
        other = [(d, r) for d, r in found if side_of(r) not in (None, side, "bilateral")]
        if other:
            return RuleOutcome(Status.UNMET, f"{display(other[0][1])} is charted, but the request is for the {side} "
                               "side. Check which side the clinic meant.",
                               [_evidence(r, d) for d, r in other])
        return RuleOutcome(Status.UNKNOWN, f"{display(found[0][1])} is charted without a side, and the request is "
                           f"for the {side} side.", [_evidence(r, d) for d, r in found])

    def _procedure(self, rule: ProcedureRule, record: PatientRecord, as_of: date) -> RuleOutcome:
        any_match, in_window = [], []
        for r in record.of_type("Procedure"):
            if not _matches(r, rule.codes) or r.get("status") in {"not-done", "entered-in-error", "preparation"}:
                continue
            start, end = procedure_period(r)
            if r.get("status") == "in-progress" and not (r.get("performedPeriod") or {}).get("end"):
                end = as_of  # still going: it counts up to the as-of date
            if start is None or start > as_of:
                continue
            any_match.append((start, end, r))
            if rule.within_days is None or (as_of - (end or start)).days <= rule.within_days:
                in_window.append((start, end, r))
        if not any_match:
            return RuleOutcome(Status.UNKNOWN, f"No {rule.label} was found in the record.")
        if not in_window:
            latest = max(e or s for s, e, _ in any_match)
            return RuleOutcome(
                Status.UNMET,
                f"The most recent {rule.label} was on {_when(latest)}, {_span((as_of - latest).days)} ago; "
                f"the policy requires one within {_span(rule.within_days or 0)}.",
                [_evidence(r, e or s) for s, e, r in any_match],
            )
        evidence = [_evidence(r, e or s) for s, e, r in sorted(in_window, key=lambda t: t[0])]
        first = min(s for s, _, _ in in_window)
        last = max(e or s for s, e, _ in in_window)
        if rule.min_count is not None and len(in_window) < rule.min_count:
            return RuleOutcome(
                Status.UNMET, f"{len(in_window)} found; the policy requires at least {rule.min_count}.", evidence
            )
        if rule.min_span_days is not None:
            span = (last - first).days
            if span < rule.min_span_days and rule.below_minimum == "unknown":
                return RuleOutcome(
                    Status.UNKNOWN,
                    f"{_cap(rule.label)} covers {_span(span)}; the policy says it usually lasts at least "
                    f"{_span(rule.min_span_days)}, so the clinician confirms whether that was a reasonable attempt.",
                    evidence)
            if span < rule.min_span_days:
                return RuleOutcome(
                    Status.UNMET,
                    f"{_cap(rule.label)} covers {_span(span)}; the policy requires at least "
                    f"{_span(rule.min_span_days)}.",
                    evidence,
                )
            return RuleOutcome(
                Status.MET, f"{_cap(rule.label)} for {_span(span)}, {_when(first)} to {_when(last)}.", evidence
            )
        ago = (as_of - last).days
        return RuleOutcome(Status.MET, f"{_cap(rule.label)} on {_when(last)}, {_span(ago)} ago.", evidence)

    def _observation(self, rule: ObservationRule, record: PatientRecord, as_of: date) -> RuleOutcome:
        found = []
        for r in record.of_type("Observation"):
            if not _matches(r, rule.codes) or r.get("status") in {"entered-in-error", "cancelled"}:
                continue
            when = observation_date(r)
            if when is None or when > as_of:
                continue
            if rule.within_days is not None and (as_of - when).days > rule.within_days:
                continue
            found.append((when, r))
        if not found:
            return RuleOutcome(Status.UNKNOWN, f"No {rule.label} was found in the record.")
        if rule.before_procedure:
            ordered = self._before(rule, record, as_of, found)
            if isinstance(ordered, RuleOutcome):
                return ordered
            found = ordered
        when, latest = max(found, key=lambda t: t[0])
        evidence = [_evidence(latest, when)]
        if rule.value_code is not None:
            ok = _matches(latest, [rule.value_code], "valueCodeableConcept")
            shown = display(latest, "valueCodeableConcept")
            verdict = Status.MET if ok else Status.UNMET
            return RuleOutcome(verdict, f"{display(latest)}: {shown.lower()}, recorded {_when(when)}.", evidence)
        value = (latest.get("valueQuantity") or {}).get("value")
        if value is None:
            return RuleOutcome(Status.UNKNOWN, f"{display(latest)} is recorded without a value.", evidence)
        ok = (rule.gte is None or value >= rule.gte) and (rule.lt is None or value < rule.lt)
        bounds = " and ".join(
            b for b in [f"at least {rule.gte:g}" if rule.gte is not None else "",
                        f"below {rule.lt:g}" if rule.lt is not None else ""] if b
        )
        verdict = Status.MET if ok else Status.UNMET
        note = f"{display(latest)} was {value:g} on {_when(when)}" + (f"; required: {bounds}." if bounds else ".")
        return RuleOutcome(verdict, note, evidence)

    def _before(self, rule: ObservationRule, record: PatientRecord, as_of: date, found: list) -> list | RuleOutcome:
        """Keep the findings dated on or before the most recent qualifying procedure in the window."""
        wanted = rule.before_procedure or []
        days = []
        for r in record.of_type("Procedure"):
            if not _matches(r, wanted) or r.get("status") in {"not-done", "entered-in-error", "preparation"}:
                continue
            start, end = procedure_period(r)
            if start is not None and start <= as_of and (rule.within_days is None
                                                         or (as_of - start).days <= rule.within_days):
                days.append(start)
        if not days:
            return RuleOutcome(Status.UNKNOWN, f"{_cap(rule.before_label)} is not in the record, so the order of the "
                               f"{rule.label} cannot be checked.", [_evidence(r, d) for d, r in found])
        test = max(days)
        before = [(d, r) for d, r in found if d <= test]
        if before:
            return before
        when, first = min(found, key=lambda t: t[0])
        return RuleOutcome(Status.UNMET, f"The {rule.label} on record is dated {_when(when)}, after "
                           f"{rule.before_label} on {_when(test)}; the policy requires it before.",
                           [_evidence(first, when)])

    @staticmethod
    def _age(rule: AgeRule, record: PatientRecord, as_of: date) -> RuleOutcome:
        born = record.patient.get("birthDate")
        if not born:
            return RuleOutcome(Status.UNKNOWN, "The record has no date of birth.")
        b = date.fromisoformat(born[:10])
        years = as_of.year - b.year - ((as_of.month, as_of.day) < (b.month, b.day))
        if years >= rule.min_years:
            return RuleOutcome(Status.MET, f"The patient is {years}.")
        return RuleOutcome(Status.UNMET, f"The patient is {years}; the policy covers patients {rule.min_years} and "
                           "older.")

    def _any_of(self, rule: AnyOf, record: PatientRecord, as_of: date, side: str | None = None) -> RuleOutcome:
        outcomes = [self._rule(r, record, as_of, side) for r in rule.rules]
        met = [o for o in outcomes if o.status == Status.MET]
        if met:
            return RuleOutcome(Status.MET, " ".join(o.detail for o in met), [e for o in met for e in o.evidence])
        everything = [e for o in outcomes for e in o.evidence]
        if all(o.status == Status.UNMET for o in outcomes):
            return RuleOutcome(Status.UNMET, " ".join(o.detail for o in outcomes), everything)
        return RuleOutcome(Status.UNKNOWN, " ".join(o.detail for o in outcomes), everything)

    def _all_of(self, rule: AllOf, record: PatientRecord, as_of: date, side: str | None = None) -> RuleOutcome:
        outcomes = [self._rule(r, record, as_of, side) for r in rule.rules]
        everything = [e for o in outcomes for e in o.evidence]
        detail = " ".join(o.detail for o in outcomes)
        if any(o.status == Status.UNMET for o in outcomes):
            return RuleOutcome(Status.UNMET, detail, everything)
        if all(o.status == Status.MET for o in outcomes):
            return RuleOutcome(Status.MET, detail, everything)
        return RuleOutcome(Status.UNKNOWN, detail, everything)


class UnapprovedCriteriaSet(Exception):
    """A draft criteria set, or one changed since a reviewer approved it."""
