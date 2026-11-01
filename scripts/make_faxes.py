"""Render the demo's faxed referral cover sheets for synthetic patients, clean, typical and degraded, with the
expected value of every field beside them (faxes.json) for the evaluation.

    python3 scripts/make_faxes.py data/faxes      # needs Pillow and the DejaVu fonts; output is committed
"""

from __future__ import annotations

import hashlib
import json
import random
import sys
from datetime import date
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter, ImageFont

ROOT = Path(__file__).resolve().parents[1]
FONT = "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"
BOLD = "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"
REFERRALS = {"2026-1023": "clean", "2026-1027": "typical", "2026-1029": "degraded"}
DIAGNOSIS = {"E0601": "G47.33 Obstructive sleep apnea", "27447": "M17.11 Primary osteoarthritis, right knee",
             "72148": "M54.50 Low back pain, unspecified"}
CLINICS = {"pract-patel": "Mesa Verde Sleep Center", "pract-lee": "Mesa Verde Orthopedics, Rio Rancho",
           "pract-reyes": "Mesa Verde Orthopedics, Santa Fe"}


def fields_for(case: dict) -> dict[str, str]:
    bundle = json.loads((ROOT / "data/fhir/patients" / f"{case['patient_id']}.json").read_text())
    patient = next(e["resource"] for e in bundle["entry"] if e["resource"]["resourceType"] == "Patient")
    name = patient["name"][0]
    born = date.fromisoformat(patient["birthDate"])
    digits = int(hashlib.sha256(case["id"].encode()).hexdigest(), 16) % 10**5
    return {
        "referral": case["id"],
        "patient_name": f"{name['given'][0]} {name['family']}",
        "birth_date": f"{born:%m/%d/%Y}",
        "member_id": f"MV{digits:05d}{case['payer']['member_id_last4']}",
        "payer": case["payer"]["name"],
        "clinician": case["ordering_clinician"]["name"],
        "service_code": case["service"]["code"],
        "service": case["service"]["display"],
        "diagnosis": DIAGNOSIS[case["service"]["code"]],
    }


LABELS = [("referral", "Referral number"), ("patient_name", "Patient name"), ("birth_date", "Date of birth"),
          ("member_id", "Member ID"), ("payer", "Health plan"), ("clinician", "Ordering clinician"),
          ("service_code", "CPT/HCPCS code"), ("service", "Requested service"), ("diagnosis", "Diagnosis")]


def render(case: dict, values: dict[str, str], quality: str, seed: int) -> Image.Image:
    w, h = 1700, 2200  # US letter at 200 dpi, a standard fine-mode fax
    page = Image.new("L", (w, h), 255)
    d = ImageDraw.Draw(page)
    title, body, small = ImageFont.truetype(BOLD, 44), ImageFont.truetype(FONT, 34), ImageFont.truetype(FONT, 26)
    clinic = CLINICS[case["ordering_clinician"]["id"]]
    d.text((120, 110), "PRIOR AUTHORIZATION REFERRAL", font=title, fill=0)
    d.text((120, 175), f"From: {clinic}    Fax: (505) 555-01{seed % 90 + 10}", font=small, fill=0)
    d.text((120, 212), "To: Mesa Verde Health, Prior Authorization Team    Pages: 1 of "
                       f"{len(case['packet']) + 1}", font=small, fill=0)
    d.line((120, 270, w - 120, 270), fill=0, width=3)
    y = 330
    for key, label in LABELS:
        d.text((120, y), f"{label}: {values[key]}", font=body, fill=0)
        y += 92
    d.line((120, y + 10, w - 120, y + 10), fill=0, width=2)
    notes = ("Clinical notes are attached as the following pages. Please contact the ordering clinician's office with "
             "any questions about this request.")
    words, line, y = notes.split(), "", y + 50
    for word in words:
        if d.textlength(f"{line} {word}", font=small) > w - 240:
            d.text((120, y), line.strip(), font=small, fill=0)
            line, y = "", y + 40
        line += f" {word}"
    d.text((120, y), line.strip(), font=small, fill=0)
    d.text((120, h - 260), f"Signed: {case['ordering_clinician']['name']}", font=body, fill=0)
    d.text((120, h - 160), "CONFIDENTIAL: contains protected health information. Synthetic data for a demo.",
           font=small, fill=0)

    rng = random.Random(seed)  # noqa: S311 - speckle on a picture, not a secret
    if quality == "clean":
        return page.point(lambda v: 0 if v < 140 else 255, "1")
    angle = {"typical": 0.6, "degraded": 1.4}[quality]
    page = page.rotate(angle, resample=Image.BICUBIC, fillcolor=255, expand=False)
    if quality == "degraded":
        page = page.resize((w // 2, h // 2), Image.BILINEAR).filter(ImageFilter.GaussianBlur(0.9)).resize((w, h))
    px = page.load()
    for _ in range({"typical": 2_500, "degraded": 26_000}[quality]):  # speckle from the phone line and the glass
        x, yy = rng.randrange(w), rng.randrange(h)
        px[x, yy] = 0 if px[x, yy] > 128 else 255
    return page.point(lambda v: 0 if v < 150 else 255, "1")


def main(out: Path) -> None:
    cases = {c["id"]: c for c in json.loads((ROOT / "data/cases/cases.json").read_text())}
    out.mkdir(parents=True, exist_ok=True)
    manifest = {}
    for n, (case_id, quality) in enumerate(REFERRALS.items(), start=1):
        fax_id = f"FAX-{n:04d}"
        values = fields_for(cases[case_id])
        render(cases[case_id], values, quality, seed=n * 7919).save(out / f"{fax_id}.png", optimize=True)
        manifest[fax_id] = {"file": f"{fax_id}.png", "quality": quality, "expected": values}
    (out / "faxes.json").write_text(json.dumps(manifest, indent=2) + "\n")
    print(f"Wrote {len(manifest)} faxes to {out}")


if __name__ == "__main__":
    main(Path(sys.argv[1] if len(sys.argv) > 1 else ROOT / "data/faxes"))
