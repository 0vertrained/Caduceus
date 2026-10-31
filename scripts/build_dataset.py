"""Build the Caduceus development dataset from a raw Synthea export.

    python scripts/build_dataset.py --synthea <synthea-out>/fhir --out data

1. Pick living Synthea patients whose generated history fits each scenario (for example, a patient Synthea gave
   essential hypertension for the CPAP case that qualifies through a comorbidity).
2. Trim each bundle to the recent, relevant part of the record (Synthea writes full lifetimes).
3. Write a per-case overlay: the referral packet as DocumentReference pages, and the structured resources the
   criteria need, each pointing back to the sentence in the note it came from.
4. Write data/cases/cases.json with the expected determination for every criterion. Labels are known by
   construction, which is what makes the evals honest.

Everything is deterministic for a given Synthea export.
"""

from __future__ import annotations

import argparse
import base64
import json
import uuid
from datetime import date, timedelta
from pathlib import Path

AS_OF = date(2026, 10, 28)
KEEP_SINCE = AS_OF - timedelta(days=3 * 365)
NS = uuid.UUID("6c8f3c2e-3d4a-4b7a-9a52-0f5c4a1a2330")

SNOMED = "http://snomed.info/sct"
ICD10 = "http://hl7.org/fhir/sid/icd-10-cm"
CPT = "http://www.ama-assn.org/go/cpt"
LOCAL = "http://mesaverde.example/fhir/CodeSystem/caduceus"
SOURCE_EXT = "http://mesaverde.example/fhir/StructureDefinition/source-sentence"

CLINICIANS = {
    "reyes": {"id": "pract-reyes", "name": "Dr. Ana Reyes", "specialty": "Orthopedics"},
    "lee": {"id": "pract-lee", "name": "Dr. Marcus Lee", "specialty": "Orthopedics"},
    "patel": {"id": "pract-patel", "name": "Dr. Priya Patel", "specialty": "Sleep medicine"},
}

SERVICES = {
    "lumbar": {"system": "cpt", "code": "72148", "display": "MRI of the lumbar spine without contrast",
               "short": "MRI of the lumbar spine", "criteria_set": "dev-lumbar-mri@2026-09"},
    "knee": {"system": "cpt", "code": "27447", "display": "Total knee replacement",
             "short": "Total knee replacement", "criteria_set": "dev-knee-arthroplasty@2026-09"},
    "cpap": {"system": "hcpcs", "code": "E0601", "display": "CPAP device for sleep apnea",
             "short": "CPAP device", "criteria_set": "dev-cpap-osa@2026-09"},
}


def rid(*parts: str) -> str:
    return str(uuid.uuid5(NS, "/".join(parts)))


def d(days_before: int) -> str:
    return (AS_OF - timedelta(days=days_before)).isoformat()


def coding(system: str, code: str, display: str) -> dict:
    return {"coding": [{"system": system, "code": code, "display": display}], "text": display}


def source(doc_id: str, sentence: str) -> list[dict]:
    return [{"url": SOURCE_EXT, "extension": [
        {"url": "document", "valueReference": {"reference": f"DocumentReference/{doc_id}"}},
        {"url": "sentence", "valueString": sentence}]}]


class Overlay:
    def __init__(self, case_id: str, patient_id: str):
        self.case_id, self.patient_id = case_id, patient_id
        self.resources: list[dict] = []
        self.pages: list[str] = []

    @property
    def subject(self) -> dict:
        return {"reference": f"Patient/{self.patient_id}"}

    def note(self, key: str, title: str, when: str, author: str, sentences: list[str]) -> tuple[str, list[str]]:
        doc_id = rid(self.case_id, "doc", key)
        text = " ".join(sentences)
        self.resources.append({
            "resourceType": "DocumentReference", "id": doc_id, "status": "current",
            "type": coding(LOCAL, "packet-page", title), "subject": self.subject, "date": when + "T09:00:00-06:00",
            "description": title, "author": [{"display": author}],
            "content": [{"attachment": {"contentType": "text/plain", "title": title,
                                        "data": base64.b64encode(text.encode()).decode()}}],
        })
        self.pages.append(doc_id)
        return doc_id, sentences

    def condition(self, key, system, code, display, onset_days, doc=None, sentence=None, status="active"):
        r = {"resourceType": "Condition", "id": rid(self.case_id, "cond", key), "subject": self.subject,
             "clinicalStatus": coding("http://terminology.hl7.org/CodeSystem/condition-clinical", status, status),
             "code": coding(system, code, display), "onsetDateTime": d(onset_days), "recordedDate": d(onset_days)}
        if doc:
            r["extension"] = source(doc, sentence)
        self.resources.append(r)

    def procedure(self, key, code, display, start_days, end_days=None, doc=None, sentence=None):
        r = {"resourceType": "Procedure", "id": rid(self.case_id, "proc", key), "status": "completed",
             "subject": self.subject, "code": coding(CPT, code, display)}
        if end_days is None:
            r["performedDateTime"] = d(start_days) + "T10:00:00-06:00"
        else:
            r["performedPeriod"] = {"start": d(start_days), "end": d(end_days)}
        if doc:
            r["extension"] = source(doc, sentence)
        self.resources.append(r)

    def observation(self, key, code, display, days, value=None, value_code=None, doc=None, sentence=None):
        r = {"resourceType": "Observation", "id": rid(self.case_id, "obs", key), "status": "final",
             "subject": self.subject, "code": coding(LOCAL, code, display),
             "effectiveDateTime": d(days) + "T10:00:00-06:00"}
        if value is not None:
            r["valueQuantity"] = {"value": value, "unit": "events/h", "system": "http://unitsofmeasure.org",
                                  "code": "/h"}
        if value_code is not None:
            r["valueCodeableConcept"] = coding(LOCAL, value_code[0], value_code[1])
        if doc:
            r["extension"] = source(doc, sentence)
        self.resources.append(r)

    def bundle(self) -> dict:
        return {"resourceType": "Bundle", "id": rid(self.case_id, "overlay"), "type": "collection",
                "entry": [{"fullUrl": f"urn:uuid:{r['id']}", "resource": r} for r in self.resources]}


# Scenario builders ------------------------------------------------------------------------------------

def referral(o: Overlay, svc: dict, clin: dict, dx: str, received_days: int = 1) -> None:
    o.note("referral", "Referral form", d(received_days + 1), clin["name"], [
        f"Requested service: {svc['display']} ({svc['system'].upper()} {svc['code']}).",
        f"Diagnosis: {dx}.",
        f"Ordering clinician: {clin['name']}, {clin['specialty']}.",
        "Priority: routine.",
    ])


def lumbar(o: Overlay, variant: str, synthea_lbp: bool) -> dict:
    clin = CLINICIANS["reyes"]
    referral(o, SERVICES["lumbar"], clin, "M54.50, low back pain")
    onset = {"undocumented_red_flags": 63, "all_met": 84, "too_early": 21, "stale_xray": 84}[variant]
    s1 = f"Patient reports low back pain radiating to the left buttock, ongoing for approximately {onset // 7} weeks."
    visit, _ = o.note("visit", "Visit note", d(14), clin["name"], [
        "Orthopedic follow-up.", s1, "Pain is worse with prolonged sitting and eases with walking.",
        "Plan: MRI of the lumbar spine without contrast to evaluate for disc pathology."])
    if not synthea_lbp or variant != "all_met":
        o.condition("lbp", ICD10, "M54.50", "Low back pain, unspecified", onset, visit, s1)
    labels = {"lbp_duration": "unmet" if variant == "too_early" else "met"}

    if variant != "too_early":
        span = {"undocumented_red_flags": (118, 74), "all_met": (130, 72), "stale_xray": (140, 90)}[variant]
        s2 = (f"Patient completed {(span[0] - span[1]) // 7} weeks of physical therapy with minimal improvement in "
              "pain or function.")
        pt, _ = o.note("pt", "Therapy summary", d(span[1]), "Mesa Verde Physical Therapy",
                       ["Physical therapy discharge summary.", s2, "A home exercise program was provided."])
        o.procedure("pt", "97110", "Therapeutic exercise", span[0], span[1], pt, s2)
        labels["conservative_care"] = "met"
    else:
        labels["conservative_care"] = "unknown"

    xray_days = {"undocumented_red_flags": 57, "all_met": 30, "too_early": 10, "stale_xray": 171}[variant]
    s3 = "Degenerative disc disease at L4 to L5 with mild narrowing. No acute fracture or malalignment."
    xr, _ = o.note("xray", "Radiology report", d(xray_days), "Mesa Verde Imaging",
                   ["Lumbar spine radiographs, two views.", "Findings:", s3,
                    "Impression: degenerative changes, no acute abnormality."])
    o.procedure("xray", "72100", "X-ray lumbosacral spine, 2 or 3 views", xray_days, None, xr, s3)
    labels["recent_xray"] = "unmet" if variant == "stale_xray" else "met"

    if variant == "undocumented_red_flags":
        labels["red_flags_excluded"] = "unknown"
    else:
        s4 = ("No saddle anesthesia, bowel or bladder change, fever, weight loss or history of cancer; "
              "red flags negative.")
        rf, _ = o.note("redflags", "Red-flag screen", d(8), clin["name"], ["Red-flag review.", s4])
        o.observation("redflags", "red-flag-screen", "Red-flag symptom screen", 8,
                      value_code=("negative", "Negative"), doc=rf, sentence=s4)
        labels["red_flags_excluded"] = "met"
    return {"clinician": clin, "labels": labels}


def knee(o: Overlay, variant: str, synthea_oa: bool) -> dict:
    clin = CLINICIANS["lee"]
    referral(o, SERVICES["knee"], clin, "M17.11, primary osteoarthritis of the right knee")
    s1 = "Right knee pain for several years, now limiting walking to one block and making stairs difficult."
    visit, _ = o.note("visit", "Visit note", d(20), clin["name"], ["Orthopedic consultation.", s1,
                      "Exam shows crepitus and reduced range of motion."])
    # The orthopedist charts the knee with its side, even when the problem list (Synthea) says only "knee": a
    # knee replacement is for one knee, and the criteria check that the diagnosis is on that side.
    o.condition("oa", ICD10, "M17.11", "Unilateral primary osteoarthritis, right knee", 900, visit, s1)
    labels = {"oa_diagnosis": "met"}

    s2 = "Medial joint-space narrowing with osteophytes, consistent with moderate to severe osteoarthritis."
    xr, _ = o.note("xray", "Radiology report", d(138), "Mesa Verde Imaging",
                   ["Right knee radiographs, three views, weight-bearing.", s2])
    o.procedure("xray", "73562", "X-ray knee, 3 views", 138, None, xr, s2)
    o.observation("narrowing", "knee-joint-space", "Knee joint space on radiograph", 138,
                  value_code=("narrowed", "Narrowed"), doc=xr, sentence=s2)
    labels["imaging_narrowing"] = "met"

    if variant == "all_met":
        s3 = "Completed thirteen weeks of supervised physical therapy without lasting relief."
        pt, _ = o.note("pt", "Therapy summary", d(149), "Mesa Verde Physical Therapy", [s3])
        o.procedure("pt", "97110", "Therapeutic exercise", 240, 149, pt, s3)
        labels["conservative_12_weeks"] = "met"
    elif variant == "short_therapy":
        s3 = "Completed six weeks of physical therapy; the patient stopped attending after week six."
        pt, _ = o.note("pt", "Therapy summary", d(47), "Mesa Verde Physical Therapy", [s3])
        o.procedure("pt", "97110", "Therapeutic exercise", 89, 47, pt, s3)
        labels["conservative_12_weeks"] = "unknown"
    else:
        s3 = "Corticosteroid injection to the right knee with two weeks of relief."
        inj, _ = o.note("injection", "Procedure note", d(105), clin["name"], [s3])
        o.procedure("injection", "20610", "Arthrocentesis or injection, major joint", 105, None, inj, s3)
        labels["conservative_12_weeks"] = "met"

    if variant == "short_therapy":
        labels["functional_limitation"] = "unknown"
    else:
        o.observation("function", "functional-limitation", "Functional limitation assessment", 20,
                      value_code=("present", "Present"), doc=visit, sentence=s1)
        labels["functional_limitation"] = "met"

    if variant == "infection":
        s5 = "Aspiration of the right knee grew Staphylococcus aureus; septic arthritis is being treated."
        inf, _ = o.note("infection", "Infectious disease note", d(6), "Dr. Helen Ortiz", [s5])
        o.condition("infection", ICD10, "M00.861", "Arthritis due to other bacteria, right knee", 9, inf, s5)
        labels["no_active_infection"] = "unmet"
    else:
        labels["no_active_infection"] = "met"
    return {"clinician": clin, "labels": labels}


def cpap(o: Overlay, variant: str, synthea_osa: bool) -> dict:
    clin = CLINICIANS["patel"]
    referral(o, SERVICES["cpap"], clin, "G47.33, obstructive sleep apnea")
    labels: dict[str, str] = {}
    s1 = "Loud snoring and witnessed pauses in breathing reported by the patient's spouse."
    if variant != "no_sleep_eval":
        visit, _ = o.note("eval", "Sleep medicine evaluation", d(58), clin["name"],
                          ["Face-to-face sleep evaluation.", s1, "Plan: sleep study."])
        o.observation("eval", "sleep-evaluation", "Face-to-face sleep evaluation", 58,
                      value_code=("completed", "Completed"), doc=visit, sentence=s1)
        labels["face_to_face"] = "met"
    else:
        labels["face_to_face"] = "unknown"
    if not synthea_osa:
        o.condition("osa", ICD10, "G47.33", "Obstructive sleep apnea", 30)
    labels["osa_diagnosis"] = "met"

    if variant == "no_test":
        labels["sleep_test"] = "unknown"
        labels["severity_qualifies"] = "unknown"
        return {"clinician": clin, "labels": labels}

    ahi = {"severe": 22, "mild_with_htn": 9, "no_sleep_eval": 9}[variant]
    s2 = f"Apnea-hypopnea index {ahi} events per hour; lowest oxygen saturation 86 percent."
    code, name = ("95810", "Polysomnography, attended") if variant == "severe" else ("95806", "Home sleep test")
    st, _ = o.note("study", "Sleep study report", d(40), "Mesa Verde Sleep Center", [f"{name}.", s2])
    o.procedure("study", code, name, 40, None, st, s2)
    o.observation("ahi", "ahi", "Apnea-hypopnea index", 40, value=ahi, doc=st, sentence=s2)
    labels["sleep_test"] = "met"
    labels["severity_qualifies"] = "met" if variant in ("severe", "mild_with_htn") else "unknown"
    return {"clinician": clin, "labels": labels}


SCENARIOS = [
    # case id, service, variant, patient requirement, payer, due offset (hours from seeding), urgency
    ("2026-1043", "lumbar", "undocumented_red_flags", {"without": ["278860009"]}, "Humana Medicare Advantage", 1.5),
    ("2026-1041", "knee", "all_met", {"with": ["239873007"]}, "Medicare", 4),
    ("2026-1038", "cpap", "mild_with_htn", {"with": ["59621000"]}, "UnitedHealthcare Medicare Advantage", 4),
    ("2026-1036", "lumbar", "all_met", {"with": ["278860009"]}, "Medicare", 27),
    ("2026-1035", "cpap", "no_sleep_eval", {"without": ["59621000", "414545008"]}, "Medicaid managed care", 23),
    ("2026-1033", "knee", "short_therapy", {"with": ["239873007"]}, "Humana Medicare Advantage", 98),
    ("2026-1031", "cpap", "severe", {}, "Medicare", 99),
    ("2026-1029", "lumbar", "too_early", {}, "UnitedHealthcare Medicare Advantage", 120),
    ("2026-1027", "knee", "infection", {}, "Medicare", 122),
    ("2026-1025", "lumbar", "stale_xray", {}, "Humana Medicare Advantage", 140),
    ("2026-1023", "cpap", "no_test", {}, "Medicare", 145),
]


# Synthea selection and trimming --------------------------------------------------------------------------

def active_codes(bundle: dict) -> set[str]:
    out = set()
    for e in bundle["entry"]:
        r = e["resource"]
        if r["resourceType"] == "Condition" and "abatementDateTime" not in r:
            out |= {c["code"] for c in r["code"].get("coding", [])}
    return out


def patient_of(bundle: dict) -> dict:
    return next(e["resource"] for e in bundle["entry"] if e["resource"]["resourceType"] == "Patient")


def age(p: dict) -> int:
    b = date.fromisoformat(p["birthDate"])
    return AS_OF.year - b.year - ((AS_OF.month, AS_OF.day) < (b.month, b.day))


def recent(r: dict) -> bool:
    t = r["resourceType"]
    if t in ("Patient", "Condition", "AllergyIntolerance"):
        return True
    if t not in ("Procedure", "Observation", "MedicationRequest", "Encounter", "Immunization"):
        return False
    when = (r.get("effectiveDateTime") or r.get("authoredOn") or r.get("performedDateTime")
            or (r.get("performedPeriod") or {}).get("start") or (r.get("period") or {}).get("start")
            or r.get("occurrenceDateTime") or "")[:10]
    return bool(when) and date.fromisoformat(when) >= KEEP_SINCE


def close_open_periods(node: object) -> None:
    """Synthea stamps the end of anything still open on the reference date with the wall clock at generation
    time. FHIR's way to say "ongoing" is a period with no end, and it makes the output reproducible."""
    if isinstance(node, dict):
        period = node.get("period")
        if isinstance(period, dict) and period.get("end", "")[:10] > AS_OF.isoformat():
            del period["end"]
        for v in node.values():
            close_open_periods(v)
    elif isinstance(node, list):
        for v in node:
            close_open_periods(v)


def trim(bundle: dict) -> dict:
    keep = []
    for e in bundle["entry"]:
        r = e["resource"]
        if not recent(r):
            continue
        r.pop("text", None)
        close_open_periods(r)
        keep.append({"fullUrl": e.get("fullUrl"), "resource": r})
    return {"resourceType": "Bundle", "id": patient_of(bundle)["id"], "type": "collection", "entry": keep}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--synthea", type=Path, required=True)
    ap.add_argument("--out", type=Path, default=Path("data"))
    args = ap.parse_args()

    # Order by the patient's id, which is in the data, not by file name: Synthea names files after patients, and
    # an accented name ("González") is spelled differently on disk depending on the machine's locale.
    loaded = []
    for f in args.synthea.glob("*.json"):
        if f.name.startswith(("hospital", "practitioner")):
            continue
        b = json.loads(f.read_text(encoding="utf-8"))
        loaded.append((patient_of(b)["id"], f, b))
    candidates = []
    for _, f, b in sorted(loaded, key=lambda x: x[0]):
        p = patient_of(b)
        if "deceasedDateTime" in p or not 58 <= age(p) <= 86:
            continue
        candidates.append((f, active_codes(b)))

    used: set[Path] = set()
    cases, patients_dir, overlays_dir = [], args.out / "fhir" / "patients", args.out / "fhir" / "overlays"
    patients_dir.mkdir(parents=True, exist_ok=True)
    overlays_dir.mkdir(parents=True, exist_ok=True)
    builders = {"lumbar": (lumbar, "278860009"), "knee": (knee, "239873007"),
                "cpap": (cpap, "78275009")}

    for case_id, service, variant, need, payer, due_hours in SCENARIOS:
        pick = next(f for f, codes in candidates if f not in used
                    and all(c in codes for c in need.get("with", []))
                    and not any(c in codes for c in need.get("without", [])))
        used.add(pick)
        bundle = json.loads(pick.read_text(encoding="utf-8"))
        p = patient_of(bundle)
        build, own_code = builders[service]
        has_own = own_code in active_codes(bundle) or (service == "cpap" and "73430006" in active_codes(bundle))
        overlay = Overlay(case_id, p["id"])
        meta = build(overlay, variant, has_own)
        (patients_dir / f"{p['id']}.json").write_text(json.dumps(trim(bundle), separators=(",", ":")))
        (overlays_dir / f"{case_id}.json").write_text(json.dumps(overlay.bundle(), indent=1))
        svc = SERVICES[service]
        cases.append({
            "id": case_id, "patient_id": p["id"], "service_line": service, "variant": variant,
            "service": {k: svc[k] for k in ("system", "code", "display", "short")},
            **({"side": "right"} if service == "knee" else {}),
            "criteria_set": svc["criteria_set"], "as_of": AS_OF.isoformat(),
            "payer": {"name": payer, "member_id_last4": str(int(rid(case_id, "member")[:8], 16) % 10000).zfill(4)},
            "ordering_clinician": meta["clinician"], "packet": overlay.pages,
            "received": d(1), "due_in_hours": due_hours, "expected": meta["labels"],
        })
        print(f"{case_id} {service:6} {variant:24} {p['name'][0]['given'][0]} {p['name'][0]['family']} age {age(p)}")

    (args.out / "cases").mkdir(exist_ok=True)
    (args.out / "cases" / "cases.json").write_text(json.dumps(cases, indent=2) + "\n")


if __name__ == "__main__":
    main()
