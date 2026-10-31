"""The criteria engine: golden labels, the never-guess property, receipts, integrity and determinism."""

from __future__ import annotations

import base64
import copy
import json
from datetime import date

import pytest

from caduceus.criteria.dsl import CriteriaSet, Status
from caduceus.criteria.engine import Engine, UnapprovedCriteriaSet
from caduceus.fhir.record import PatientRecord
from tests.conftest import DATA

CASES = json.loads((DATA / "cases" / "cases.json").read_text())


def evaluate(engine, library, store, case):
    record = store.record(case["patient_id"], case["id"])
    return engine.evaluate(library[case["criteria_set"]], record, date.fromisoformat(case["as_of"]))


@pytest.mark.parametrize("case", CASES, ids=[f"{c['id']}-{c['variant']}" for c in CASES])
def test_golden_labels(engine, library, store, case):
    det = evaluate(engine, library, store, case)
    assert {r.criterion_id: r.status.value for r in det.results} == case["expected"]


@pytest.mark.parametrize("key", ["dev-lumbar-mri@2026-09", "dev-knee-arthroplasty@2026-09", "dev-cpap-osa@2026-09"])
def test_empty_record_is_unknown_never_met(engine, library, key):
    """With no clinical data at all, nothing is met except an exclusion a reviewer accepted as met-by-absence."""
    empty = PatientRecord(patient={"resourceType": "Patient", "id": "empty", "birthDate": "1950-01-01"})
    det = engine.evaluate(library[key], empty, date(2026, 10, 28))
    for criterion, result in zip(library[key].criteria, det.results, strict=True):
        if criterion.kind == "exclusion" and criterion.when_absent == "met":
            assert result.status == Status.MET and criterion.absent_rationale in result.explanation
        else:
            assert result.status == Status.UNKNOWN, result.criterion_id
    assert det.overall == "needs_attestation"


@pytest.mark.parametrize("case", CASES, ids=[c["id"] for c in CASES])
def test_removing_evidence_never_leaves_met(engine, library, store, case):
    """Strip the cited evidence until none is left: a criterion can end unknown or unmet, never met."""
    criteria = {c.id: c for c in library[case["criteria_set"]].criteria}
    for cid, criterion in criteria.items():
        if criterion.kind == "exclusion":
            continue
        record = store.record(case["patient_id"], case["id"])
        as_of = date.fromisoformat(case["as_of"])
        for _ in range(50):
            result = next(r for r in engine.evaluate(library[case["criteria_set"]], record, as_of).results
                          if r.criterion_id == cid)
            if result.status != Status.MET:
                break
            cited = {(e.resource_type, e.resource_id) for e in result.evidence}
            assert cited, f"{cid} is met with no evidence"
            record.resources = [r for r in record.resources if (r["resourceType"], r.get("id")) not in cited]
        assert result.status in (Status.UNKNOWN, Status.UNMET)


def test_every_met_criterion_shows_its_receipt(engine, library, store):
    """Evidence that came from the referral packet points at a real page and a sentence that is on it."""
    linked = 0
    for case in CASES:
        for r in evaluate(engine, library, store, case).results:
            if r.status == Status.MET and r.kind != "exclusion":
                assert r.evidence, r.criterion_id
            for e in r.evidence:
                if e.source_document:
                    doc = store.document(e.source_document.split("/")[-1])
                    text = base64.b64decode(doc["content"][0]["attachment"]["data"]).decode()
                    assert e.source_sentence in text
                    linked += 1
    assert linked >= 25


def test_changed_criteria_set_is_refused(engine, library):
    original = library["dev-lumbar-mri@2026-09"]
    data = original.model_dump(mode="json")
    data["criteria"][0]["rule"]["min_duration_days"] = 7  # someone loosens a threshold after approval
    tampered = CriteriaSet.model_validate(data)
    record = PatientRecord(patient={"resourceType": "Patient", "id": "p"})
    with pytest.raises(UnapprovedCriteriaSet, match="changed after approval"):
        engine.evaluate(tampered, record, date(2026, 10, 28))


def test_draft_criteria_set_is_refused(library):
    data = library["dev-cpap-osa@2026-09"].model_dump(mode="json")
    data["status"] = "draft"
    draft = CriteriaSet.model_validate(data)
    with pytest.raises(UnapprovedCriteriaSet, match="draft"):
        Engine({draft.key: draft.content_sha256()}).evaluate(
            draft, PatientRecord(patient={"resourceType": "Patient", "id": "p"}), date(2026, 10, 28))


def test_absence_as_met_only_for_reviewed_exclusions(library):
    data = copy.deepcopy(library["dev-lumbar-mri@2026-09"].model_dump(mode="json"))
    data["criteria"][3]["when_absent"] = "met"
    with pytest.raises(ValueError, match="only allowed for exclusion"):
        CriteriaSet.model_validate(data)
    data = copy.deepcopy(library["dev-knee-arthroplasty@2026-09"].model_dump(mode="json"))
    data["criteria"][4]["absent_rationale"] = None
    with pytest.raises(ValueError, match="requires an absent_rationale"):
        CriteriaSet.model_validate(data)


def test_deterministic(engine, library, store):
    case = CASES[0]
    assert evaluate(engine, library, store, case).to_dict() == evaluate(engine, library, store, case).to_dict()


def _knee_record(*resources):
    return PatientRecord(patient={"resourceType": "Patient", "id": "p", "birthDate": "1950-01-01"},
                         resources=[{"id": f"r{i}", **r} for i, r in enumerate(resources)])


def _status(engine, library, key, criterion, record, as_of=date(2026, 10, 28)):
    det = engine.evaluate(library[key], record, as_of)
    return next(r for r in det.results if r.criterion_id == criterion)


ICD = "http://hl7.org/fhir/sid/icd-10-cm"
VER = "http://terminology.hl7.org/CodeSystem/condition-ver-status"


@pytest.mark.parametrize("verification", ["refuted", "entered-in-error"])
def test_a_ruled_out_or_voided_diagnosis_does_not_count(engine, library, verification):
    infection = {"resourceType": "Condition", "code": {"coding": [{"system": ICD, "code": "M00.9"}]},
                 "onsetDateTime": "2026-09-01", "verificationStatus": {"coding": [{"system": VER,
                                                                                   "code": verification}]}}
    result = _status(engine, library, "dev-knee-arthroplasty@2026-09", "no_active_infection", _knee_record(infection))
    assert result.status == Status.MET  # the exclusion is not triggered by a diagnosis that is not real


def test_therapy_still_in_progress_counts_up_to_today(engine, library):
    pt = {"resourceType": "Procedure", "status": "in-progress",
          "code": {"coding": [{"system": "http://www.ama-assn.org/go/cpt", "code": "97110"}]},
          "performedPeriod": {"start": "2026-07-01"}}
    result = _status(engine, library, "dev-lumbar-mri@2026-09", "conservative_care", _knee_record(pt))
    assert result.status == Status.MET and "October 28, 2026" in result.explanation


def test_a_problem_charted_recently_without_an_onset_is_unknown_not_unmet(engine, library):
    def lbp(recorded):
        return {"resourceType": "Condition", "code": {"coding": [{"system": ICD, "code": "M54.50"}]},
                "clinicalStatus": {"coding": [{"code": "active"}]}, "recordedDate": recorded}

    key = "dev-lumbar-mri@2026-09"
    assert _status(engine, library, key, "lbp_duration", _knee_record(lbp("2026-10-10"))).status == Status.UNKNOWN
    assert _status(engine, library, key, "lbp_duration", _knee_record(lbp("2026-06-01"))).status == Status.MET
