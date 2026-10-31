"""Showcase cases for the public demo: three long, messy referral packets, each built to show one thing.

    python scripts/build_showcase.py --out data/showcase

- 2026-1051, a knee replacement requested for the right knee when the chart documents the left: the criteria catch
  it.
- 2026-1052, a lumbar MRI whose physical therapy is only in an outside summary scanned into the packet: the record
  cannot answer, and "where to look" points at the page.
- 2026-1053, a CPAP request for severe sleep apnea with oxygen at 71% at night and atrial fibrillation: expedited.

Built with the hard-set generator's record and packet machinery (scripts/build_hard_set.py), from fixed clinical
stories, not Synthea. Only the demo seeds them, after the 11 Synthea cases; the eval gate and the tests do not. Every
name, date and number is synthetic. Rebuilds are byte-identical.
"""

from __future__ import annotations

import argparse
import base64
import json
import random
import sys
import uuid
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from build_hard_set import (  # noqa: E402
    CPT,
    ICD10,
    NS,
    Ctx,
    build_pages,
    condition,
    day,
    describe,
    noise,
    observation,
    procedure,
)

SHOW = uuid.uuid5(NS, "showcase")
CLINICIANS = {"reyes": {"id": "pract-reyes", "name": "Dr. Ana Reyes", "specialty": "Orthopedics"},
              "lee": {"id": "pract-lee", "name": "Dr. Marcus Lee", "specialty": "Orthopedics"},
              "patel": {"id": "pract-patel", "name": "Dr. Priya Patel", "specialty": "Sleep medicine"}}
SERVICES = {"lumbar": {"system": "cpt", "code": "72148", "display": "MRI of the lumbar spine without contrast",
                       "short": "MRI of the lumbar spine"},
            "knee": {"system": "cpt", "code": "27447", "display": "Total knee replacement",
                     "short": "Total knee replacement"},
            "cpap": {"system": "hcpcs", "code": "E0601", "display": "CPAP device for sleep apnea",
                     "short": "CPAP device"}}


def wrong_knee(c: Ctx) -> dict:
    """The referral asks for the right knee; every page of the chart is about the left one."""
    c.add(condition(ICD10, "M17.12", "Unilateral primary osteoarthritis, left knee", onset=1400))
    c.add(procedure(CPT, "73562", "X-ray knee, 3 views", 75))
    c.add(observation("knee-joint-space", "Knee joint space on radiograph", 75, value="narrowed"))
    c.add(procedure(CPT, "97110", "Therapeutic exercise", 230, 130))
    c.add(observation("functional-limitation", "Functional limitation assessment", 40, value="present"))
    c.sentences += [
        ("referral", "Requested service: total knee arthroplasty, right knee (CPT 27447).", "arthritis_of_this_knee"),
        ("referral", "Diagnosis: M17.12, primary osteoarthritis of the left knee.", "arthritis_of_this_knee"),
        ("visit", "Left knee pain for six years, now limiting walking to half a block; takes stairs one at a time.",
         "functional_disability"),
        ("visit", "Exam of the left knee: crepitus, varus deformity, range of motion 5 to 95 degrees.", None),
        ("visit", "Right knee: mild ache after long walks, no effusion, full range of motion.",
         "arthritis_of_this_knee"),
        ("visit", "Plan: left total knee arthroplasty once cleared by cardiology.", "arthritis_of_this_knee"),
        ("imaging", "Standing radiographs of the left knee: severe medial joint-space narrowing with osteophytes.",
         "imaging_shows_arthritis"),
        ("therapy", "Completed 14 weeks of supervised physical therapy for the left knee without lasting relief.",
         "conservative_therapy"),
    ]
    return {"service": "knee", "side": "right", "payer": "Humana Medicare Advantage", "clinician": "lee",
            "due_in_hours": 30, "criteria_set": "knee-arthroplasty@2026-11",
            "expected": {"arthritis_of_this_knee": "unmet", "imaging_shows_arthritis": "met",
                         "conservative_therapy": "met", "functional_disability": "met", "no_active_infection": "met",
                         "no_wound_at_site": "met"}}


def therapy_only_in_the_packet(c: Ctx) -> dict:
    """Ten weeks of outside physical therapy, never charted: the summary is a scanned page in the packet."""
    c.add(condition(ICD10, "M54.16", "Radiculopathy, lumbar region", onset=160, recorded=160))
    c.add(procedure(CPT, "72100", "X-ray lumbosacral spine, 2 or 3 views", 35))
    c.add(observation("red-flag-screen", "Red-flag symptom screen", 12, value="negative"))
    c.sentences += [
        ("visit", "Low back pain radiating down the right leg to the calf for about five months.", "lbp_duration"),
        ("visit", "Red flags negative: no saddle anesthesia, bowel or bladder change, fever or weight loss.",
         "red_flags_excluded"),
        ("imaging", "Lumbar spine radiographs, two views: disc space narrowing at L4 to L5, no fracture.",
         "recent_xray"),
        ("therapy", "Outside records: Sangre de Cristo Physical Therapy discharge summary.", None),
        ("therapy", "Completed 10 weeks of physical therapy, 20 visits, core stabilization and nerve glides, with "
                    "minimal improvement in radicular pain.", "conservative_care"),
        ("visit", "She asks whether the therapy notes from Sangre de Cristo were received.", None),
    ]
    return {"service": "lumbar", "payer": "UnitedHealthcare Medicare Advantage", "clinician": "reyes",
            "due_in_hours": 22, "criteria_set": "dev-lumbar-mri@2026-09",
            "expected": {"lbp_duration": "met", "conservative_care": "unknown", "recent_xray": "met",
                         "red_flags_excluded": "met"}}


def expedited_cpap(c: Ctx) -> dict:
    """Severe sleep apnea, low oxygen at night and atrial fibrillation: the clinic asks for an expedited decision."""
    c.add(condition(ICD10, "G47.33", "Obstructive sleep apnea", onset=20))
    c.add(condition(ICD10, "I48.0", "Paroxysmal atrial fibrillation", onset=900))
    c.add(observation("sleep-evaluation", "Face-to-face sleep evaluation", 35, value="completed"))
    c.add(procedure(CPT, "95810", "Polysomnography, attended", 21))
    c.add(observation("ahi", "Apnea-hypopnea index", 21, quantity=58))
    c.sentences += [
        ("referral", "Priority: urgent. The ordering clinician requests an expedited decision.", None),
        ("sleep", "Attended polysomnography: apnea-hypopnea index 58 events per hour; lowest oxygen saturation 71 "
                  "percent.", "severity_qualifies"),
        ("visit", "In-person sleep evaluation: loud snoring, witnessed apneas, morning headaches, dozing at the wheel.",
         "in_person_evaluation"),
        ("visit", "Paroxysmal atrial fibrillation; nighttime episodes coincide with oxygen desaturation.", None),
    ]
    return {"service": "cpap", "payer": "Humana Medicare Advantage", "clinician": "patel", "due_in_hours": 6,
            "criteria_set": "cpap-osa@2026-11", "urgency": "expedited",
            "urgency_reason": "Severe sleep apnea with oxygen falling to 71% at night and paroxysmal atrial "
                              "fibrillation; waiting seven days risks harm to the heart.",
            "expected": {"adult": "met", "in_person_evaluation": "met", "covered_sleep_test": "met",
                         "severity_qualifies": "met"}}


STORIES = [("2026-1051", wrong_knee, ("Gilbert", "Romero"), date(1954, 6, 12), "male"),
           ("2026-1052", therapy_only_in_the_packet, ("Esther", "Maestas"), date(1960, 2, 3), "female"),
           ("2026-1053", expedited_cpap, ("Raymond", "Vigil"), date(1963, 9, 27), "male")]
MALE_ONLY = {"N40.0"}  # the background conditions are drawn at random; a woman's record must not hold these


def build(out: Path) -> None:
    for sub in ("cases", "fhir/patients", "fhir/overlays"):
        (out / sub).mkdir(parents=True, exist_ok=True)
    cases = []
    for case_id, story, (given, family), born, sex in STORIES:
        rng = random.Random(f"showcase/{case_id}")  # noqa: S311 (reproducible demo data, not security)
        patient_id = str(uuid.uuid5(SHOW, f"patient/{case_id}"))
        c = Ctx("dev", rng, case_id, patient_id, "")
        noise(c)
        if sex == "female":
            c.resources = [r for r in c.resources if r["code"]["coding"][0]["code"] not in MALE_ONLY]
        background = len(c.resources)
        meta = story(c)
        c.service = meta["service"]
        pages, gold = build_pages(c, f"{given} {family}", born.isoformat())
        docs = [{"resourceType": "DocumentReference", "id": str(uuid.uuid5(SHOW, f"{case_id}/page/{i}")),
                 "status": "current", "description": title, "type": {"text": title}, "date": day(1) + "T08:00:00Z",
                 "subject": {"reference": f"Patient/{patient_id}"},
                 "content": [{"attachment": {"contentType": "text/plain",
                                             "data": base64.b64encode(text.encode()).decode()}}]}
                for i, (title, text) in enumerate(pages, start=1)]
        patient = {"resourceType": "Patient", "id": patient_id, "name": [{"given": [given], "family": family}],
                   "gender": sex, "birthDate": born.isoformat()}
        (out / "fhir/patients" / f"{patient_id}.json").write_text(
            json.dumps({"resourceType": "Bundle", "type": "collection", "entry": [{"resource": patient}]}, indent=1))
        (out / "fhir/overlays" / f"{case_id}.json").write_text(json.dumps(
            {"resourceType": "Bundle", "type": "collection",
             "entry": [{"resource": r} for r in c.resources + docs]}, indent=1))
        last4 = str(int(uuid.uuid5(SHOW, f"member/{case_id}").hex[:8], 16) % 10000).zfill(4)
        cases.append({"id": case_id, "patient_id": patient_id, "service_line": meta["service"],
                      "variant": story.__name__, "service": SERVICES[meta["service"]],
                      **({"side": meta["side"]} if meta.get("side") else {}),
                      "criteria_set": meta["criteria_set"], "as_of": "2026-10-28",
                      "payer": {"name": meta["payer"], "member_id_last4": last4},
                      "ordering_clinician": CLINICIANS[meta["clinician"]], "packet": [d["id"] for d in docs],
                      "received": day(1), "due_in_hours": meta["due_in_hours"],
                      **({"urgency": meta["urgency"], "urgency_reason": meta["urgency_reason"]}
                         if meta.get("urgency") else {}),
                      "expected": meta["expected"], "evidence": gold,
                      "record": [describe(r) for r in c.resources[background:]]})
        print(f"{case_id} {meta['service']:6} {len(pages)} pages")
    (out / "cases" / "cases.json").write_text(json.dumps(cases, indent=1) + "\n")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", type=Path, default=Path("data/showcase"))
    build(ap.parse_args().out)
