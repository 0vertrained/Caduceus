"""Build the hard evaluation sets: long, messy referral packets with labels known by construction.

    python scripts/build_hard_set.py --out data/hard

Two splits from two seeds: `dev` (18 cases) and `heldout` (36 cases, never tuned against, worded from its own phrase
banks). Each criterion gets a state from the lists below, and the state fixes the structured resources, the packet
sentences that bear on the criterion, and the expected answer, written from the policy rather than by running the
engine. Text found only in the packet never makes a criterion met. Packets run to 7-14 shuffled pages, a quarter of
them OCR-damaged, and everything is synthetic.
"""

from __future__ import annotations

import argparse
import base64
import json
import random
import uuid
from dataclasses import dataclass, field
from datetime import date, timedelta
from pathlib import Path

AS_OF = date(2026, 10, 28)
NS = uuid.UUID("2b1f6a8e-4c3d-4e5f-9a10-7c6d5e4f3a21")
SPLITS = {"dev": (20261031, 6), "heldout": (20261101, 12)}  # seed, cases per service line

ICD10 = "http://hl7.org/fhir/sid/icd-10-cm"
SNOMED = "http://snomed.info/sct"
CPT = "http://www.ama-assn.org/go/cpt"
HCPCS = "https://www.cms.gov/Medicare/Coding/HCPCSReleaseCodeSets"
LOINC = "http://loinc.org"
LOCAL = "http://mesaverde.example/fhir/CodeSystem/caduceus"
CLINICAL = "http://terminology.hl7.org/CodeSystem/condition-clinical"
VERIFY = "http://terminology.hl7.org/CodeSystem/condition-ver-status"

SERVICES = {
    "lumbar": ("dev-lumbar-mri@2026-09", {"system": "cpt", "code": "72148",
                                          "display": "MRI of the lumbar spine without contrast"}),
    "knee": ("dev-knee-arthroplasty@2026-09", {"system": "cpt", "code": "27447",
                                               "display": "Total knee arthroplasty"}),
    "cpap": ("dev-cpap-osa@2026-09", {"system": "hcpcs", "code": "E0601",
                                      "display": "CPAP device for obstructive sleep apnea"}),
}

FIRST = ["Elena", "Marcus", "Rosa", "Walter", "Juana", "Harold", "Lucia", "Dennis", "Carmen", "Gilbert", "Irene",
         "Tomas", "Beatriz", "Ronald", "Esther", "Arturo", "Gloria", "Leonard", "Pilar", "Frank", "Ines", "Raymond"]
LAST = ["Montoya", "Archuleta", "Baca", "Trujillo", "Lucero", "Sandoval", "Romero", "Chavez", "Gallegos", "Vigil",
        "Martinez", "Ortiz", "Padilla", "Roybal", "Quintana", "Salazar", "Tafoya", "Valdez", "Maestas", "Duran"]
CLINICIANS = {"lumbar": ("Dr. Ana Reyes", "Orthopedics"), "knee": ("Dr. Marcus Lee", "Orthopedic Surgery"),
              "cpap": ("Dr. Priya Patel", "Sleep Medicine")}

# Evidence phrasings. Each bank is split: dev cases draw only from "dev", held-out cases only from "heldout".
BANKS: dict[str, dict[str, list[str]]] = {
    "lbp_duration": {
        "dev": ["Patient reports low back pain radiating to the left buttock, ongoing for {span}.",
                "Chief complaint: lower back pain present for {span}, worse with sitting."],
        "heldout": ["Lumbago x {span_short}, axial with intermittent radiation to the posterior thigh.",
                    "She describes aching in the lumbar region that began {span} ago and has not let up.",
                    "HPI: {span} of LBP, no inciting trauma."]},
    "lbp_onset": {
        "dev": ["Low back pain began on {date} after lifting a box."],
        "heldout": ["Back pain onset {date} while gardening; no fall.",
                    "Symptoms of lumbar pain date to {date} per patient."]},
    "lbp_chronic": {
        "dev": ["Long-standing low back pain for many years, recently charted."],
        "heldout": ["Chronic lumbago going back years; added to the problem list at this visit."]},
    "lbp_recent": {
        "dev": ["Low back pain started about {span} ago."],
        "heldout": ["Back pain is new, roughly {span} in duration."]},
    "lbp_error": {
        "dev": ["Note: the earlier chronic back pain entry was charted in error on the wrong patient."],
        "heldout": ["Problem list corrected: prior lumbago entry belonged to another chart and was voided."]},
    "pt_course": {
        "dev": ["Physical therapy discharge summary: patient completed {weeks} weeks of physical therapy with "
                "minimal improvement in pain or function.",
                "Completed a {weeks}-week course of physical therapy from {start} to {end}."],
        "heldout": ["Therapy D/C: {visits} visits over {weeks} wks of PT, core stabilization and McKenzie "
                    "extension; goals partially met.",
                    "Rehab summary: physiotherapy {start} through {end}, plateaued, referred back to ortho."]},
    "pt_session": {
        "dev": ["Physical therapy daily note, visit {n} of {total}: therapeutic exercise and manual therapy."],
        "heldout": ["PT treatment note #{n}/{total}, therapeutic ex 97110 x 3 units, tolerated well."]},
    "pt_stopped": {
        "dev": ["Physical therapy was stopped after {visits} visits because the patient could not attend."],
        "heldout": ["Discharged from PT after {visits} sessions for non-attendance; plan of care not completed."]},
    "pt_ongoing": {
        "dev": ["Physical therapy is ongoing since {start}, currently in week {weeks}."],
        "heldout": ["Remains in active PT (started {start}), {weeks} wks in, attending 2x/wk."]},
    "pt_not_done": {
        "dev": ["A physical therapy referral was placed but the patient never scheduled."],
        "heldout": ["PT order on file; pt did not schedule the evaluation."]},
    "pt_outside": {
        "dev": ["She completed {weeks} weeks of physical therapy at an outside clinic last spring."],
        "heldout": ["Reports a prior course of PT elsewhere, about {weeks} wks, records not available."]},
    "xray_lumbar": {
        "dev": ["Lumbar spine radiographs, {views} views, {date}: degenerative disc disease at L4 to L5 with mild "
                "narrowing, no acute fracture or malalignment.",
                "X-ray of the lumbar spine on {date} shows multilevel spondylosis without fracture."],
        "heldout": ["XR L-spine {views}V ({date}): facet arthropathy L4-S1, alignment maintained.",
                    "Plain films of the lumbosacral spine dated {date}: mild disc height loss at L5-S1."]},
    "xray_lumbar_outside": {
        "dev": ["X-rays of the lower back at urgent care last month were reportedly unremarkable."],
        "heldout": ["Pt states L-spine films were done at an outside facility, no report enclosed."]},
    "redflag_negative": {
        "dev": ["Red flags negative: no saddle anesthesia, bowel or bladder change, fever, weight loss or history "
                "of cancer.",
                "Denies urinary retention, incontinence, fever or unexplained weight loss."],
        "heldout": ["Cauda equina screen: denies saddle numbness, no bladder or bowel dysfunction, afebrile, "
                    "no hx of malignancy.",
                    "Screened for red-flag symptoms; none present."]},
    "redflag_positive": {
        "dev": ["New urinary retention and saddle numbness reported this week."],
        "heldout": ["Positive red-flag screen: unintentional 15 lb weight loss and night sweats."]},
    "redflag_old_positive": {
        "dev": ["At the prior visit she had a fever with a urinary infection, since resolved."],
        "heldout": ["Earlier screen flagged fever (UTI), treated; repeat screen today is clear."]},
    "oa_dx": {
        "dev": ["Assessment: primary osteoarthritis of the {side} knee.",
                "Diagnosis: {side} knee osteoarthritis, Kellgren-Lawrence grade 3."],
        "heldout": ["Impression: DJD {side} knee, tricompartmental.",
                    "End-stage gonarthrosis, {side}, failed nonoperative care."]},
    "oa_other_side": {
        "dev": ["Osteoarthritis is documented in the {other} knee only."],
        "heldout": ["OA confined to the {other} knee per prior films; {side} knee exam benign."]},
    "xray_knee": {
        "dev": ["Weight-bearing knee radiographs on {date}: medial joint-space narrowing with osteophytes."],
        "heldout": ["Standing AP/lat {side} knee ({date}): severe medial compartment narrowing, subchondral "
                    "sclerosis."]},
    "xray_knee_preserved": {
        "dev": ["Knee radiographs on {date}: joint spaces preserved, no osteophytes."],
        "heldout": ["{side} knee films ({date}) unremarkable; joint space maintained."]},
    "pt_knee": {
        "dev": ["Completed {weeks} weeks of supervised physical therapy for the knee without lasting relief."],
        "heldout": ["Knee rehab {start} to {end} ({weeks} wks), quad strengthening, pain persists."]},
    "injection": {
        "dev": ["Corticosteroid injection to the {side} knee on {date} gave two weeks of relief."],
        "heldout": ["S/p intra-articular Kenalog 40 mg {side} knee {date}, transient benefit only."]},
    "injection_outside": {
        "dev": ["She had a cortisone shot in the knee at another office a few months ago."],
        "heldout": ["Reports a prior steroid injection elsewhere, date unknown."]},
    "function_present": {
        "dev": ["Knee pain limits walking to one block and makes stairs difficult.",
                "Functional assessment: unable to complete daily activities without stopping."],
        "heldout": ["Ambulates with a cane, needs the rail for stairs, cannot get up from a low chair.",
                    "ADLs limited by knee pain; stopped grocery shopping."]},
    "function_absent": {
        "dev": ["Functional assessment: no limitation of daily activities."],
        "heldout": ["Independent with ADLs, walks two miles daily without difficulty."]},
    "infection_active": {
        "dev": ["Aspiration of the {side} knee grew Staphylococcus aureus; septic arthritis is being treated."],
        "heldout": ["Septic {side} knee, cultures positive, on IV cefazolin."]},
    "infection_history": {
        "dev": ["History of septic arthritis of the knee in {year}, treated and resolved."],
        "heldout": ["Remote joint infection ({year}), completed antibiotics, no recurrence."]},
    "infection_ruled_out": {
        "dev": ["Suspected joint infection was ruled out: aspirate cultures negative."],
        "heldout": ["Infection workup negative; septic arthritis excluded."]},
    "osa_dx": {
        "dev": ["Diagnosis: obstructive sleep apnea."],
        "heldout": ["Assessment: OSA, new diagnosis."]},
    "sleep_test": {
        "dev": ["Polysomnography on {date}: apnea-hypopnea index {ahi} events per hour; lowest oxygen saturation "
                "{spo2} percent.",
                "Home sleep test performed {date}; apnea-hypopnea index {ahi}."],
        "heldout": ["HSAT {date}: REI {ahi}/h, nadir SpO2 {spo2}%.",
                    "Attended PSG ({date}) AHI {ahi}, supine predominant."]},
    "sleep_test_outside": {
        "dev": ["A sleep study was done at another hospital last year; results not enclosed."],
        "heldout": ["Reports a prior PSG out of state, report pending."]},
    "sleepiness": {
        "dev": ["Reports excessive daytime sleepiness, Epworth score {ess}."],
        "heldout": ["ESS {ess}/24; dozes off while driving at red lights."]},
    "htn": {
        "dev": ["Hypertension, on lisinopril."],
        "heldout": ["HTN, BP 152/94 today, on amlodipine."]},
    "hhd": {
        "dev": ["Hypertensive heart disease without heart failure."],
        "heldout": ["Hypertensive heart disease, LVH on echo."]},
    "f2f": {
        "dev": ["Face-to-face sleep evaluation on {date}: loud snoring and witnessed pauses in breathing reported "
                "by the spouse."],
        "heldout": ["Sleep medicine consult in person {date}: snoring, witnessed apneas, morning headaches."]},
    "f2f_outside": {
        "dev": ["Seen by a sleep doctor elsewhere before the study, per the patient."],
        "heldout": ["Prior in-person sleep consultation reported at an outside clinic; note not available."]},
}

BOILERPLATE = [
    "Allergies: no known drug allergies.", "Medications reviewed and reconciled.",
    "Vital signs: BP 132/78, HR 72, temperature 98.2 F.", "Social history: former smoker, quit 2009.",
    "Review of systems otherwise negative except as noted in the HPI.", "Denies chest pain or shortness of breath.",
    "Patient was counseled on weight management and activity.", "Follow up in four weeks or sooner if worse.",
    "This note was dictated using voice recognition software and may contain errors.",
    "Electronically signed by the attending clinician.", "Patient verbalized understanding of the plan.",
    "Gait steady; no assistive device in the office today.", "Skin warm and dry.", "Lungs clear to auscultation.",
    "Heart regular rate and rhythm.", "Abdomen soft, nontender.", "Neurologically intact to light touch.",
    "Fall risk screening completed.", "Advance directive discussed.", "Influenza vaccine offered.",
    "Diet: regular.", "Lives with spouse in a single-story home.", "Copied forward from the prior visit.",
    "Plan discussed with the patient and family.", "Labs ordered as below.",
]
DISTRACTORS = {
    "lumbar": ["Chest X-ray: no acute cardiopulmonary disease.", "Cervical strain in 2021 resolved with heat.",
               "Physical therapy for rotator cuff tendinopathy in 2022.", "DEXA scan: osteopenia of the hip.",
               "Knee X-ray last year showed mild arthritis."],
    "knee": ["Right shoulder X-ray: AC joint arthrosis.", "Physical therapy for low back pain in 2021.",
             "Chest radiograph: clear.", "Influenza injection given in the fall.", "Hand osteoarthritis noted."],
    "cpap": ["Chest X-ray: no acute disease.", "Insomnia treated with sleep hygiene in 2020.",
             "Daytime fatigue attributed to anemia last year.", "Hypothyroidism on levothyroxine.",
             "Snoring partner reports improved with side sleeping."],
}

NOISE_CONDITIONS = [("E11.9", "Type 2 diabetes mellitus"), ("E78.5", "Hyperlipidemia"), ("K21.9", "GERD"),
                    ("J44.9", "COPD"), ("N18.30", "Chronic kidney disease, stage 3"), ("F32.A", "Depression"),
                    ("M81.0", "Osteoporosis"), ("E03.9", "Hypothyroidism"), ("J30.9", "Allergic rhinitis"),
                    ("R73.03", "Prediabetes"), ("H40.9", "Glaucoma"), ("N40.0", "Benign prostatic hyperplasia")]
NOISE_PROCEDURES = [("45378", "Colonoscopy"), ("90686", "Influenza vaccine"), ("93306", "Echocardiogram"),
                    ("77080", "DEXA scan"), ("71046", "Chest X-ray, 2 views"), ("99213", "Office visit"),
                    ("93000", "Electrocardiogram"), ("36415", "Venipuncture")]
NOISE_OBSERVATIONS = [("8480-6", "Systolic blood pressure", "mm[Hg]", 110, 150), ("4548-4", "Hemoglobin A1c", "%",
                      5.2, 8.9), ("39156-5", "Body mass index", "kg/m2", 21, 41), ("13457-7", "LDL cholesterol",
                      "mg/dL", 60, 170), ("2160-0", "Creatinine", "mg/dL", 0.6, 1.6)]


@dataclass
class Ctx:
    split: str
    rng: random.Random
    case_id: str
    patient_id: str
    service: str
    side: str = "right"
    resources: list[dict] = field(default_factory=list)
    sentences: list[tuple[str, str, str | None]] = field(default_factory=list)  # (page kind, text, gold criterion)
    counter: int = 0
    test_day: int | None = None  # days before as-of of the sleep test, when there is one in the record
    override: str | None = None  # set by a build when another criterion's state changes this one's answer

    def rid(self) -> str:
        self.counter += 1
        return str(uuid.uuid5(NS, f"{self.case_id}/{self.counter}"))

    def say(self, kind: str, bank: str, criterion: str | None, **values) -> None:
        text = self.rng.choice(BANKS[bank][self.split]).format(**values)
        self.sentences.append((kind, text, criterion))

    def add(self, resource: dict) -> None:
        resource["id"] = self.rid()
        resource["subject"] = {"reference": f"Patient/{self.patient_id}"}
        self.resources.append(resource)


def day(n: int) -> str:
    return (AS_OF - timedelta(days=n)).isoformat()


def spoken(d: str, rng: random.Random) -> str:
    x = date.fromisoformat(d)
    return rng.choice([f"{x:%m/%d/%Y}", f"{x:%B} {x.day}, {x.year}", x.isoformat(), f"{x:%b} {x.day}"])


def span(days: int) -> str:
    return f"{days // 7} weeks" if days < 120 else f"{round(days / 30.4)} months" if days < 730 else \
        f"{days // 365} years"


def concept(system: str, code: str, text: str) -> dict:
    return {"coding": [{"system": system, "code": code, "display": text}], "text": text}


def condition(system, code, text, onset=None, recorded=None, clinical="active", verification=None, abated=None):
    r: dict = {"resourceType": "Condition", "code": concept(system, code, text)}
    if clinical:
        r["clinicalStatus"] = concept(CLINICAL, clinical, clinical)
    if verification:
        r["verificationStatus"] = concept(VERIFY, verification, verification)
    if onset is not None:
        r["onsetDateTime"] = day(onset)
    if recorded is not None:
        r["recordedDate"] = day(recorded)
    if abated is not None:
        r["abatementDateTime"] = day(abated)
    return r


def procedure(system, code, text, start, end=None, status="completed", ongoing=False):
    r: dict = {"resourceType": "Procedure", "status": status, "code": concept(system, code, text)}
    if ongoing:
        r["performedPeriod"] = {"start": day(start)}
    elif end is None:
        r["performedDateTime"] = day(start) + "T10:00:00Z"
    else:
        r["performedPeriod"] = {"start": day(start), "end": day(end)}
    return r


def observation(code, text, when, value=None, quantity=None, status="final"):
    r: dict = {"resourceType": "Observation", "status": status, "code": concept(LOCAL, code, text),
               "effectiveDateTime": day(when) + "T10:00:00Z"}
    if value is not None:
        r["valueCodeableConcept"] = concept(LOCAL, value, value.capitalize())
    if quantity is not None:
        r["valueQuantity"] = {"value": quantity, "unit": "/h"}
    return r


# States: (name, weight, expected, build). `build` adds resources and sentences for one criterion. -------------

def lumbar_states():
    def lbp(c, onset, *, code=("M54.50", "Low back pain, unspecified")):
        c.add(condition(ICD10, code[0], code[1], onset=onset, recorded=onset))

    def clear(c):
        d = c.rng.randint(60, 700)
        lbp(c, d, code=c.rng.choice([("M54.50", "Low back pain, unspecified"), ("M54.16", "Radiculopathy, lumbar "
                                     "region"), ("M54.59", "Other low back pain")]))
        c.say("visit", "lbp_duration", "lbp_duration", span=span(d), span_short=span(d).replace(" weeks", " wks"))

    def edge(n):
        def build(c):
            lbp(c, n)
            c.say("visit", "lbp_onset", "lbp_duration", date=spoken(day(n), c.rng))
        return build

    def short(c):
        d = c.rng.randint(10, 30)
        lbp(c, d)
        c.say("visit", "lbp_recent", "lbp_duration", span=span(d))

    def retired(c):  # M54.5 was split into M54.50/51/59 in October 2021; older charts still carry it
        d = c.rng.randint(400, 1500)
        c.add(condition(ICD10, "M54.5", "Low back pain", onset=d, recorded=d))
        c.say("visit", "lbp_duration", "lbp_duration", span=span(d), span_short=span(d))

    def no_onset_recent(c):  # charted recently, no onset: the duration is not known from the record
        c.add(condition(ICD10, "M54.50", "Low back pain, unspecified", recorded=c.rng.randint(15, 35)))
        c.say("visit", "lbp_chronic", "lbp_duration")

    def no_onset_old(c):  # charted long ago: the pain has lasted at least that long
        c.add(condition(ICD10, "M54.50", "Low back pain, unspecified", recorded=c.rng.randint(80, 400)))
        c.say("problems", "lbp_chronic", "lbp_duration")

    def notes_only(c):
        d = c.rng.randint(60, 300)
        c.say("visit", "lbp_duration", "lbp_duration", span=span(d), span_short=span(d))

    def entered_in_error(c):  # an old entry voided as charted in error; the real one is three weeks old
        c.add(condition(ICD10, "M54.50", "Low back pain, unspecified", onset=500, recorded=500, clinical=None,
                        verification="entered-in-error"))
        d = c.rng.randint(15, 30)
        lbp(c, d)
        c.say("visit", "lbp_recent", "lbp_duration", span=span(d))
        c.say("problems", "lbp_error", "lbp_duration")

    def pt(c, start, length, status="completed", ongoing=False):
        c.add(procedure(CPT, "97110", "Therapeutic exercise", start, None if ongoing else start - length,
                        status=status, ongoing=ongoing))

    def pt_met(c):
        start, length = c.rng.randint(90, 300), c.rng.randint(45, 84)
        pt(c, start, length)
        c.say("therapy", "pt_course", "conservative_care", weeks=length // 7, visits=length // 7 * 2,
              start=spoken(day(start), c.rng), end=spoken(day(start - length), c.rng))

    def pt_sessions(c):
        start, total = c.rng.randint(80, 200), c.rng.randint(10, 16)
        length = c.rng.randint(49, 70)
        for i in range(total):
            c.add(procedure(CPT, "97110", "Therapeutic exercise", start - round(i * length / (total - 1))))
        for n in sorted(c.rng.sample(range(1, total + 1), 2)):
            c.say("therapy", "pt_session", "conservative_care", n=n, total=total)

    def pt_edge(length):
        def build(c):
            start = c.rng.randint(80, 200)
            pt(c, start, length)
            c.say("therapy", "pt_course", "conservative_care", weeks=length // 7, visits=length // 7 * 2,
                  start=spoken(day(start), c.rng), end=spoken(day(start - length), c.rng))
        return build

    def pt_stopped(c):
        start, length = c.rng.randint(60, 200), c.rng.randint(10, 20)
        pt(c, start, length, status="stopped")
        c.say("therapy", "pt_stopped", "conservative_care", visits=c.rng.randint(2, 4))

    def pt_ongoing(c):  # in progress, no end date yet: what counts is the time so far
        start = c.rng.randint(50, 80)
        pt(c, start, 0, status="in-progress", ongoing=True)
        c.say("therapy", "pt_ongoing", "conservative_care", start=spoken(day(start), c.rng), weeks=start // 7)

    def pt_old(c):
        start, length = c.rng.randint(450, 650), 60
        pt(c, start, length)
        c.say("therapy", "pt_course", "conservative_care", weeks=8, visits=16, start=spoken(day(start), c.rng),
              end=spoken(day(start - length), c.rng))

    def pt_not_done(c):
        c.add(procedure(CPT, "97161", "PT evaluation, low complexity", c.rng.randint(30, 90), status="not-done"))
        c.say("visit", "pt_not_done", "conservative_care")

    def pt_outside(c):
        c.say("visit", "pt_outside", "conservative_care", weeks=c.rng.randint(6, 10))

    def xray(c, d, code=("72100", "X-ray lumbosacral spine, 2 or 3 views")):
        c.add(procedure(CPT, code[0], code[1], d))
        c.say("imaging", "xray_lumbar", "recent_xray", views=c.rng.choice([2, 3]), date=spoken(day(d), c.rng))

    def x_met(c):
        xray(c, c.rng.randint(7, 85), c.rng.choice([("72100", "X-ray lumbosacral spine, 2 or 3 views"),
                                                    ("72110", "X-ray lumbosacral spine, 4 or more views")]))

    def x_edge(n):
        return lambda c: xray(c, n)

    def x_wrong_part(c):  # a recent chest film and an old lumbar one
        c.add(procedure(CPT, "71046", "Chest X-ray, 2 views", c.rng.randint(10, 40)))
        xray(c, c.rng.randint(150, 300))

    def x_other_code(c):  # 72114 (complete, with bending views) is a plain lumbar radiograph too
        xray(c, c.rng.randint(10, 60), ("72114", "X-ray lumbosacral spine, complete, with bending views"))

    def x_outside(c):
        c.say("visit", "xray_lumbar_outside", "recent_xray")

    def screen(c, d, value):
        c.add(observation("red-flag-screen", "Red-flag symptom screen", d, value=value))

    def r_met(c):
        screen(c, c.rng.randint(5, 100), "negative")
        c.say("visit", "redflag_negative", "red_flags_excluded")

    def r_positive(c):
        screen(c, c.rng.randint(5, 60), "positive")
        c.say("visit", "redflag_positive", "red_flags_excluded")

    def r_old_neg_new_pos(c):
        screen(c, c.rng.randint(60, 110), "negative")
        screen(c, c.rng.randint(3, 30), "positive")
        c.say("visit", "redflag_positive", "red_flags_excluded")

    def r_old_pos_new_neg(c):
        screen(c, c.rng.randint(60, 110), "positive")
        screen(c, c.rng.randint(3, 30), "negative")
        c.say("visit", "redflag_old_positive", "red_flags_excluded")
        c.say("visit", "redflag_negative", "red_flags_excluded")

    def r_stale(c):
        screen(c, c.rng.randint(150, 300), "negative")
        c.say("visit", "redflag_negative", "red_flags_excluded")

    def r_eie_positive(c):  # a positive screen voided as entered in error; the valid screen is negative
        c.add(observation("red-flag-screen", "Red-flag symptom screen", c.rng.randint(3, 20), value="positive",
                          status="entered-in-error"))
        screen(c, c.rng.randint(30, 100), "negative")
        c.say("visit", "redflag_negative", "red_flags_excluded")

    nothing = lambda c: None  # noqa: E731
    return {
        "lbp_duration": [("clear", 4, "met", clear), ("edge_42_days", 1, "met", edge(42)),
                         ("edge_41_days", 1, "unmet", edge(41)), ("short", 1, "unmet", short),
                         ("retired_code", 1, "met", retired), ("no_onset_recent", 1, "unknown", no_onset_recent),
                         ("no_onset_old", 1, "met", no_onset_old), ("notes_only", 1, "unknown", notes_only),
                         ("entered_in_error", 1, "unmet", entered_in_error)],
        "conservative_care": [("met", 4, "met", pt_met), ("sessions", 1, "met", pt_sessions),
                              ("edge_42_days", 1, "met", pt_edge(42)), ("edge_41_days", 1, "unmet", pt_edge(41)),
                              ("stopped", 1, "unmet", pt_stopped), ("ongoing", 1, "met", pt_ongoing),
                              ("old", 1, "unmet", pt_old), ("not_done", 1, "unknown", pt_not_done),
                              ("notes_only", 1, "unknown", pt_outside)],
        "recent_xray": [("met", 4, "met", x_met), ("edge_90_days", 1, "met", x_edge(90)),
                        ("edge_91_days", 1, "unmet", x_edge(91)), ("wrong_body_part", 1, "unmet", x_wrong_part),
                        ("other_code", 1, "met", x_other_code), ("missing", 1, "unknown", nothing),
                        ("notes_only", 1, "unknown", x_outside)],
        "red_flags_excluded": [("met", 4, "met", r_met), ("positive", 1, "unmet", r_positive),
                               ("newer_positive", 1, "unmet", r_old_neg_new_pos),
                               ("newer_negative", 1, "met", r_old_pos_new_neg), ("stale", 1, "unknown", r_stale),
                               ("voided_positive", 1, "met", r_eie_positive), ("missing", 1, "unknown", nothing)],
    }


def knee_states():
    oa_codes = {"right": ("M17.11", "Unilateral primary osteoarthritis, right knee"),
                "left": ("M17.12", "Unilateral primary osteoarthritis, left knee")}
    other = {"right": "left", "left": "right"}

    def oa_met(c):
        code = c.rng.choice([oa_codes[c.side], ("M17.0", "Bilateral primary osteoarthritis of knee")])
        c.add(condition(ICD10, code[0], code[1], onset=c.rng.randint(300, 2000)))
        c.say("visit", "oa_dx", "oa_diagnosis", side=c.side)

    def oa_other_side(c):  # the record's osteoarthritis is in the knee that is not being replaced
        code = oa_codes[other[c.side]]
        c.add(condition(ICD10, code[0], code[1], onset=c.rng.randint(300, 2000)))
        c.say("visit", "oa_other_side", "oa_diagnosis", side=c.side, other=other[c.side])

    def oa_unspecified(c):  # M17.9, osteoarthritis of knee, unspecified: still knee osteoarthritis
        c.add(condition(ICD10, "M17.9", "Osteoarthritis of knee, unspecified", onset=c.rng.randint(300, 2000)))
        c.say("visit", "oa_dx", "oa_diagnosis", side=c.side)

    def oa_notes_only(c):
        c.say("imaging", "oa_dx", "oa_diagnosis", side=c.side)

    def knee_xray(c, d, finding):
        c.add(procedure(CPT, "73562", "X-ray knee, 3 views", d))
        if finding:
            c.add(observation("knee-joint-space", "Knee joint space on radiograph", d, value=finding))

    def i_met(c):
        d = c.rng.randint(20, 300)
        knee_xray(c, d, "narrowed")
        c.say("imaging", "xray_knee", "imaging_narrowing", date=spoken(day(d), c.rng), side=c.side.capitalize())

    def i_preserved(c):
        d = c.rng.randint(20, 300)
        knee_xray(c, d, "preserved")
        c.say("imaging", "xray_knee_preserved", "imaging_narrowing", date=spoken(day(d), c.rng),
              side=c.side.capitalize())

    def i_old(c):
        d = c.rng.randint(400, 600)
        knee_xray(c, d, "narrowed")
        c.say("imaging", "xray_knee", "imaging_narrowing", date=spoken(day(d), c.rng), side=c.side.capitalize())

    def i_finding_missing(c):  # the report says narrowing, but no structured finding was charted
        d = c.rng.randint(20, 300)
        knee_xray(c, d, None)
        c.say("imaging", "xray_knee", "imaging_narrowing", date=spoken(day(d), c.rng), side=c.side.capitalize())

    def pt(c, start, length, ongoing=False):
        c.add(procedure(CPT, "97110", "Therapeutic exercise", start, None if ongoing else start - length,
                        status="in-progress" if ongoing else "completed", ongoing=ongoing))

    def k_pt(c):
        start, length = c.rng.randint(150, 500), c.rng.randint(90, 140)
        pt(c, start, length)
        c.say("therapy", "pt_knee", "conservative_12_weeks", weeks=length // 7, start=spoken(day(start), c.rng),
              end=spoken(day(start - length), c.rng))

    def k_injection(c):
        d = c.rng.randint(30, 300)
        c.add(procedure(CPT, "20610", "Arthrocentesis or injection, major joint", d))
        c.say("visit", "injection", "conservative_12_weeks", side=c.side, date=spoken(day(d), c.rng))

    def k_pt_short(c):  # 83 days of therapy and no injection on record: not enough, but an injection is unknown
        start = c.rng.randint(150, 400)
        pt(c, start, 83)
        c.say("therapy", "pt_knee", "conservative_12_weeks", weeks=11, start=spoken(day(start), c.rng),
              end=spoken(day(start - 83), c.rng))

    def k_pt_ongoing(c):
        start = c.rng.randint(95, 130)
        pt(c, start, 0, ongoing=True)
        c.say("therapy", "pt_ongoing", "conservative_12_weeks", start=spoken(day(start), c.rng), weeks=start // 7)

    def k_old_injection(c):
        d = c.rng.randint(400, 520)
        c.add(procedure(CPT, "20610", "Arthrocentesis or injection, major joint", d))
        c.say("visit", "injection", "conservative_12_weeks", side=c.side, date=spoken(day(d), c.rng))

    def k_outside(c):
        c.say("visit", "injection_outside", "conservative_12_weeks")

    def f(c, d, value, bank):
        c.add(observation("functional-limitation", "Functional limitation assessment", d, value=value))
        c.say("visit", bank, "functional_limitation")

    def n_active(c):
        code = {"right": ("M00.861", "Arthritis due to other bacteria, right knee"),
                "left": ("M00.862", "Arthritis due to other bacteria, left knee")}[c.side]
        c.add(condition(ICD10, code[0], code[1], onset=c.rng.randint(10, 40)))
        c.say("visit", "infection_active", "no_active_infection", side=c.side)

    def n_resolved(c):
        d = c.rng.randint(400, 900)
        c.add(condition(ICD10, "M00.9", "Pyogenic arthritis, unspecified", onset=d, clinical="resolved",
                        abated=d - 60))
        c.say("problems", "infection_history", "no_active_infection", year=(AS_OF - timedelta(days=d)).year)

    def n_refuted(c):  # suspected, then ruled out: charted as a refuted diagnosis
        c.add(condition(ICD10, "M00.9", "Pyogenic arthritis, unspecified", onset=c.rng.randint(20, 90),
                        clinical=None, verification="refuted"))
        c.say("visit", "infection_ruled_out", "no_active_infection")

    def n_entered_in_error(c):
        c.add(condition(ICD10, "M00.9", "Pyogenic arthritis, unspecified", onset=c.rng.randint(20, 90),
                        clinical=None, verification="entered-in-error"))

    nothing = lambda c: None  # noqa: E731
    return {
        "oa_diagnosis": [("met", 5, "met", oa_met), ("other_knee", 1, "unmet", oa_other_side),
                         ("unspecified_code", 1, "met", oa_unspecified), ("notes_only", 1, "unknown", oa_notes_only),
                         ("missing", 1, "unknown", nothing)],
        "imaging_narrowing": [("met", 4, "met", i_met), ("preserved", 1, "unmet", i_preserved),
                              ("old", 1, "unmet", i_old), ("finding_not_charted", 1, "unknown", i_finding_missing)],
        "conservative_12_weeks": [("therapy", 3, "met", k_pt), ("injection", 3, "met", k_injection),
                                  ("therapy_83_days", 1, "unknown", k_pt_short),
                                  ("therapy_ongoing", 1, "met", k_pt_ongoing),
                                  ("old_injection", 1, "unknown", k_old_injection),
                                  ("notes_only", 1, "unknown", k_outside)],
        "functional_limitation": [
            ("met", 4, "met", lambda c: f(c, c.rng.randint(5, 150), "present", "function_present")),
            ("absent", 1, "unmet", lambda c: f(c, c.rng.randint(5, 150), "absent", "function_absent")),
            ("stale", 1, "unknown", lambda c: f(c, c.rng.randint(220, 400), "present", "function_present")),
            ("notes_only", 1, "unknown", lambda c: c.say("visit", "function_present", "functional_limitation"))],
        "no_active_infection": [("none", 5, "met", nothing), ("active", 1, "unmet", n_active),
                                ("resolved", 1, "met", n_resolved), ("refuted", 1, "met", n_refuted),
                                ("entered_in_error", 1, "met", n_entered_in_error)],
    }


def cpap_states():
    def a_met(c):
        code = c.rng.choice([(ICD10, "G47.33", "Obstructive sleep apnea"),
                             (SNOMED, "78275009", "Obstructive sleep apnea syndrome")])
        c.add(condition(*code, onset=c.rng.randint(20, 200)))
        c.say("visit", "osa_dx", "osa_diagnosis")

    def a_eie(c):  # the only diagnosis was voided as entered in error
        c.add(condition(ICD10, "G47.33", "Obstructive sleep apnea", onset=c.rng.randint(20, 200), clinical=None,
                        verification="entered-in-error"))

    def test(c, d, code=("95810", "Polysomnography, attended")):
        c.test_day = d
        c.add(procedure(CPT if code[0][0].isdigit() else HCPCS, code[0], code[1], d))

    def t_met(c):
        test(c, c.rng.randint(25, 300), c.rng.choice([("95810", "Polysomnography, attended"),
                                                       ("95806", "Home sleep test"),
                                                       ("G0399", "Home sleep test, type III")]))

    def t_edge(n):
        return lambda c: test(c, n)

    def t_other_code(c):  # 95811, attended polysomnography with CPAP titration: an attended sleep test
        test(c, c.rng.randint(25, 300), ("95811", "Polysomnography with CPAP titration"))

    def t_outside(c):
        c.say("visit", "sleep_test_outside", "sleep_test")

    def ahi(c, value, d=None, status="final"):
        d = d if d is not None else (c.test_day if c.test_day is not None else c.rng.randint(30, 200))
        if d > 365 and status == "final":
            c.override = "unknown"  # the index comes from a test more than a year old, so it does not count
        c.add(observation("ahi", "Apnea-hypopnea index", d, quantity=value, status=status))
        if status == "final":
            c.say("sleep", "sleep_test", "severity_qualifies", date=spoken(day(d), c.rng), ahi=value,
                  spo2=c.rng.randint(78, 89))

    def v_severe(c):
        ahi(c, c.rng.randint(15, 45))

    def v_15(c):
        ahi(c, 15)

    def v_mild(extra):
        def build(c):
            ahi(c, c.rng.randint(5, 14))
            extra(c)
        return build

    def htn(c):
        c.add(condition(ICD10, "I10", "Essential hypertension", onset=c.rng.randint(400, 3000)))
        c.say("problems", "htn", "severity_qualifies")

    def eds(c):
        c.add(observation("excessive-daytime-sleepiness", "Excessive daytime sleepiness", c.rng.randint(20, 150),
                          value="present"))
        c.say("visit", "sleepiness", "severity_qualifies", ess=c.rng.randint(11, 19))

    def hhd(c):  # I11.9, hypertensive heart disease: a form of hypertension
        c.add(condition(ICD10, "I11.9", "Hypertensive heart disease without heart failure",
                        onset=c.rng.randint(400, 3000)))
        c.say("problems", "hhd", "severity_qualifies")

    def v_below5(c):
        ahi(c, round(c.rng.uniform(2, 4.9), 1))

    def v_old(c):  # the only index is from a study more than a year old (charted as a result, no procedure)
        ahi(c, c.rng.randint(20, 40), c.rng.randint(400, 500))

    def v_voided_high(c):
        ahi(c, c.rng.randint(25, 40), status="entered-in-error")
        ahi(c, c.rng.randint(5, 14))

    def evaluation(c, before: bool):
        t = c.test_day
        d = (t + c.rng.randint(10, 60)) if before else max(t - c.rng.randint(3, 20), 1)
        if d > 365:
            c.override = "unknown"  # an evaluation before a test near the year's edge is itself out of date
        c.add(observation("sleep-evaluation", "Face-to-face sleep evaluation", d, value="completed"))
        c.say("visit", "f2f", "face_to_face", date=spoken(day(d), c.rng))

    def e_stale(c):
        d = c.rng.randint(380, 500)
        c.add(observation("sleep-evaluation", "Face-to-face sleep evaluation", d, value="completed"))
        c.say("visit", "f2f", "face_to_face", date=spoken(day(d), c.rng))

    nothing = lambda c: None  # noqa: E731
    return {
        "osa_diagnosis": [("met", 6, "met", a_met), ("missing", 1, "unknown", nothing),
                          ("entered_in_error", 1, "unknown", a_eie)],
        "sleep_test": [("met", 5, "met", t_met), ("edge_365_days", 1, "met", t_edge(365)),
                       ("edge_366_days", 1, "unmet", t_edge(366)), ("other_code", 1, "met", t_other_code),
                       ("notes_only", 1, "unknown", t_outside)],
        "severity_qualifies": [("severe", 3, "met", v_severe), ("index_15", 1, "met", v_15),
                               ("mild_with_hypertension", 1, "met", v_mild(htn)),
                               ("mild_with_sleepiness", 1, "met", v_mild(eds)),
                               ("mild_alone", 1, "unknown", v_mild(lambda c: None)),
                               ("mild_with_hypertensive_heart_disease", 1, "met", v_mild(hhd)),
                               ("below_5", 1, "unmet", v_below5), ("old_index_only", 1, "unknown", v_old),
                               ("voided_high_index", 1, "unknown", v_voided_high)],
        "face_to_face": [("before_test", 4, "met", lambda c: evaluation(c, True)),
                         ("after_test", 1, "unmet", lambda c: evaluation(c, False)),
                         ("stale", 1, "unknown", e_stale), ("missing", 1, "unknown", nothing),
                         ("notes_only", 1, "unknown",
                          lambda c: c.say("visit", "f2f_outside", "face_to_face"))],
    }


STATES = {"lumbar": lumbar_states(), "knee": knee_states(), "cpap": cpap_states()}


def describe(r: dict) -> str:
    """One line a person can check a label against, without reading FHIR."""
    code = r["code"]["coding"][0]
    what = f"{r['resourceType']} {code['code']} {code['display']}"
    bits = []
    if r["resourceType"] == "Condition":
        bits += [f"onset {r['onsetDateTime']}" if "onsetDateTime" in r else "no onset date"]
        bits += [f"charted {r['recordedDate']}"] if "recordedDate" in r else []
        bits += [f"clinical status {r['clinicalStatus']['coding'][0]['code']}"] if "clinicalStatus" in r else []
        bits += [f"verification {r['verificationStatus']['coding'][0]['code']}"] if "verificationStatus" in r else []
        bits += [f"resolved {r['abatementDateTime']}"] if "abatementDateTime" in r else []
    elif r["resourceType"] == "Procedure":
        if "performedPeriod" in r:
            p = r["performedPeriod"]
            bits += [f"{p['start']} to {p.get('end', 'ongoing')}"]
        else:
            bits += [r["performedDateTime"][:10]]
        bits += [f"status {r['status']}"]
    else:
        value = (r.get("valueCodeableConcept") or {}).get("text") or (r.get("valueQuantity") or {}).get("value")
        bits += [f"{r['effectiveDateTime'][:10]}", f"value {value}", f"status {r['status']}"]
    return f"{what}: " + ", ".join(bits)


def pick(rng: random.Random, options, allowed=lambda name: True):
    options = [o for o in options if allowed(o[0])]
    return rng.choices(options, weights=[o[1] for o in options])[0]


# Packets ---------------------------------------------------------------------------------------------------

PAGE_TITLES = {"cover": "Fax cover sheet", "referral": "Prior authorization request", "problems": "Problem list "
               "and medications", "visit": "Visit note", "therapy": "Therapy notes", "imaging": "Imaging report",
               "sleep": "Sleep study report", "labs": "Laboratory results", "insurance": "Insurance and "
               "demographics", "questionnaire": "Patient questionnaire"}
OCR = [("l", "1"), ("i", "l"), ("o", "0"), ("e", "c"), ("rn", "m"), ("m", "rn"), ("s", "5"), ("a", "o")]


def damage(text: str, rng: random.Random) -> str:
    """OCR-like damage: a few character swaps and a word broken across a line."""
    out = text
    for _ in range(max(1, len(text) // 45)):
        a, b = rng.choice(OCR)
        spots = [i for i in range(len(out)) if out.startswith(a, i) and out[i - 1:i].isalpha()]
        if spots:
            i = rng.choice(spots)
            out = out[:i] + b + out[i + len(a):]
    long_words = [w for w in out.split(" ") if len(w) > 9 and w.isalpha()]
    if long_words and rng.random() < 0.5:
        w = rng.choice(long_words)
        out = out.replace(w, w[:5] + "-\n" + w[5:], 1)
    return out


def build_pages(c: Ctx, name: str, born: str) -> tuple[list[tuple[str, str]], dict[str, list[dict]]]:
    rng = c.rng
    kinds = ["cover", "referral", "problems", "visit", "visit", "labs", "insurance"]
    kinds += {"lumbar": ["therapy", "imaging"], "knee": ["therapy", "imaging", "questionnaire"],
              "cpap": ["sleep", "questionnaire"]}[c.service]
    kinds += rng.sample(["visit", "labs", "therapy", "imaging", "questionnaire"], rng.randint(0, 3))
    kinds = kinds[:14]
    body: dict[int, list[tuple[str, str | None]]] = {i: [] for i in range(len(kinds))}
    for kind, text, gold in c.sentences:
        where = [i for i, k in enumerate(kinds) if k == kind] or [i for i, k in enumerate(kinds) if k == "visit"]
        body[rng.choice(where)].append((text, gold))
    for i in range(len(kinds)):
        filler = rng.sample(BOILERPLATE, rng.randint(6, 14)) + rng.sample(DISTRACTORS[c.service], 2)
        for f in filler:
            body[i].insert(rng.randint(0, len(body[i])), (f, None))
    order = list(range(2)) + rng.sample(range(2, len(kinds)), len(kinds) - 2)  # cover and referral stay first
    pages, gold = [], {}
    clinician, specialty = CLINICIANS[c.service]
    for n, i in enumerate(order, start=1):
        kind = kinds[i]
        degraded = rng.random() < 0.25
        header = f"Mesa Verde Health · {PAGE_TITLES[kind]} · Patient: {name} · DOB {born} · Page {n} of {len(order)}"
        text, spans = header + "\n", []
        for sentence, crit in body[i]:
            s = damage(sentence, rng) if degraded else sentence
            sep = "\n" if rng.random() < 0.3 else " "
            start = len(text)
            text += s + sep
            if crit:
                spans.append((crit, start, start + len(s), s))
        text += f"Electronically signed: {clinician}, {specialty}."
        pages.append((PAGE_TITLES[kind], text))
        for crit, s, e, said in spans:
            gold.setdefault(crit, []).append({"page": n, "start": s, "end": e, "text": said})
    return pages, gold


def noise(c: Ctx) -> None:
    rng = c.rng
    for code, text in rng.sample(NOISE_CONDITIONS, rng.randint(3, 8)):
        c.add(condition(ICD10, code, text, onset=rng.randint(200, 4000)))
    for _ in range(rng.randint(15, 60)):
        code, text = rng.choice(NOISE_PROCEDURES)
        c.add(procedure(CPT, code, text, rng.randint(1, 1100)))
    for _ in range(rng.randint(20, 80)):
        code, text, unit, lo, hi = rng.choice(NOISE_OBSERVATIONS)
        c.add({"resourceType": "Observation", "status": "final", "code": concept(LOINC, code, text),
               "effectiveDateTime": day(rng.randint(1, 1100)) + "T09:00:00Z",
               "valueQuantity": {"value": round(rng.uniform(lo, hi), 1), "unit": unit}})


def build_case(split: str, rng: random.Random, n: int, service: str) -> tuple[dict, dict, dict]:
    case_id = f"H{'D' if split == 'dev' else 'H'}-{n:03d}"
    patient_id = str(uuid.uuid5(NS, f"patient/{case_id}"))
    c = Ctx(split, rng, case_id, patient_id, service, side=rng.choice(["right", "left"]))
    name = f"{rng.choice(FIRST)} {rng.choice(LAST)}"
    born = date(rng.randint(1937, 1971), rng.randint(1, 12), rng.randint(1, 28)).isoformat()
    noise(c)
    states, expected, record = {}, {}, {}
    for crit, options in STATES[service].items():
        allowed = (lambda s: c.test_day is not None or s not in ("before_test", "after_test")) \
            if crit == "face_to_face" else (lambda s: True)
        state, _, label, build = pick(rng, options, allowed)
        c.override, before = None, len(c.resources)
        build(c)
        states[crit], expected[crit] = state, c.override or label
        record[crit] = [describe(r) for r in c.resources[before:]]
    pages, gold = build_pages(c, name, born)
    docs = []
    for i, (title, text) in enumerate(pages, start=1):
        docs.append({"resourceType": "DocumentReference", "id": str(uuid.uuid5(NS, f"{case_id}/page/{i}")),
                     "status": "current", "description": title, "type": {"text": title},
                     "date": day(1) + "T08:00:00Z", "subject": {"reference": f"Patient/{patient_id}"},
                     "content": [{"attachment": {"contentType": "text/plain",
                                                 "data": base64.b64encode(text.encode()).decode()}}]})
    given, family = name.split(" ")
    patient = {"resourceType": "Patient", "id": patient_id, "name": [{"given": [given], "family": family}],
               "gender": rng.choice(["female", "male"]), "birthDate": born}
    criteria_set, svc = SERVICES[service]
    case = {"id": case_id, "patient_id": patient_id, "service_line": service, "criteria_set": criteria_set,
            "service": svc, "side": c.side if service == "knee" else None, "as_of": AS_OF.isoformat(),
            "packet": [d["id"] for d in docs], "states": states, "expected": expected, "record": record,
            "evidence": gold}
    bundle = {"resourceType": "Bundle", "type": "collection", "entry": [{"resource": patient}]}
    overlay = {"resourceType": "Bundle", "type": "collection",
               "entry": [{"resource": r} for r in c.resources + docs]}
    return case, bundle, overlay


ORDINARY = {"met", "clear", "none", "therapy", "injection", "severe", "before_test"}


def spotcheck(cases: list[dict], rng: random.Random, n: int = 15) -> str:
    """A sample for a person to check by hand: two of every three items have an unusual record."""
    items = [(c, k) for c in cases for k in c["expected"]]
    unusual = [i for i in items if i[0]["states"][i[1]] not in ORDINARY]
    usual = [i for i in items if i[0]["states"][i[1]] in ORDINARY]
    chosen = rng.sample(unusual, 10) + rng.sample(usual, n - 10)
    lines = ["# Held-out labels to check by hand", "",
             "Each item gives the policy, what the structured record holds, and what the packet says. Tick it if",
             "the expected answer is what a careful prior-authorization reviewer would conclude from the structured",
             "record under the policy. Text found only in the packet does not make a criterion met: that is what",
             "the clinician's attestation is for. Note anything you disagree with; a wrong label is a finding.", ""]
    for c, k in sorted(chosen, key=lambda i: (i[0]["id"], i[1])):
        side = f", {c['side']} knee" if c["side"] else ""
        lines += [f"- [ ] **{c['id']} {k}** ({c['service']['display']}{side}): expected **{c['expected'][k]}**",
                  f"  - Record: {'; '.join(c['record'][k]) or 'nothing charted for this criterion'}"]
        for g in c["evidence"].get(k, []):
            lines.append(f"  - Packet, page {g['page']}: \"{' '.join(g['text'].split())}\"")
    return "\n".join(lines) + "\n"


def build(out: Path) -> None:
    for split, (seed, per_line) in SPLITS.items():
        rng = random.Random(seed)  # noqa: S311 (reproducible test data, not security)
        root = out / split
        for sub in ("cases", "fhir/patients", "fhir/overlays"):
            (root / sub).mkdir(parents=True, exist_ok=True)
        cases, n = [], 0
        for _ in range(per_line):
            for service in ("lumbar", "knee", "cpap"):
                n += 1
                case, bundle, overlay = build_case(split, rng, n, service)
                cases.append(case)
                (root / "fhir/patients" / f"{case['patient_id']}.json").write_text(json.dumps(bundle, indent=1))
                (root / "fhir/overlays" / f"{case['id']}.json").write_text(json.dumps(overlay, indent=1))
        (root / "cases" / "cases.json").write_text(json.dumps(cases, indent=1) + "\n")
        if split == "heldout":
            (root / "spotcheck.md").write_text(spotcheck(cases, random.Random(seed + 1)))  # noqa: S311
        print(f"{split}: {len(cases)} cases, {sum(len(c['expected']) for c in cases)} criteria, "
              f"{sum(len(c['packet']) for c in cases)} pages")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", type=Path, default=Path("data/hard"))
    build(ap.parse_args().out)
