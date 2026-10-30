# 6. Route by confidence: low-confidence extraction goes to a person

Status: Accepted, built (`src/caduceus/intake.py`), with OCR rather than a vision-language model

## Context

Vision-language extraction from faxed referrals will be wrong sometimes, and wrong more often on degraded pages.
A wrong extracted field (a date of service, a diagnosis code) can turn into a wrong determination downstream.

## Decision

Every extracted field carries a confidence and the page region it came from. Below a per-field threshold, the
field is shown to the PA specialist to confirm against the page image before the engine runs. Thresholds are set
from the held-out evaluation set, per quality tier (clean, typical, degraded), to hold field precision at the
target while measuring how many fields go to people.

## Consequences

* The determination is never built on an unconfirmed low-confidence field.
* The confirmation step costs specialist time; the case study reports the share of fields routed to people, so
  the trade-off is visible.
* Confidence calibration must be checked per model version; a model upgrade re-runs the threshold fit.

## What was built, and why OCR

A faxed referral is identifiable, so ADR 1 allows it only on an in-VPC model. Sending the page image to an external
vision-language model would break that rule, and the demo has no in-VPC model. So the page is read by Tesseract OCR
inside the container, with no network. A vision-language model running in-VPC can replace it behind the same tool
(`documents.read_fax`) without changing what follows.

Each field carries its OCR confidence (the lowest word's, lowered when stray marks had to be stripped) and its
region on the page. A field goes to a person when it is below 90%, fails validation (date format, member ID shape,
a known payer, clinician or service code, an ICD-10 diagnosis), or is missing. Confirming requires every field to
be valid and the patient to match exactly one record by name and date of birth.

How a person checks it, so that checking is deliberate rather than a glance:

* The fields are in the order the cover sheet gives them. The health plan, the clinician and the service are chosen
  from the ones on file, starting on the closest to what was read; clinicians are narrowed to the clinic named on the
  "From:" line. A starting choice is only a suggestion: every field sent to a person needs a "Matches the fax" tick,
  or a change from where the form started, and the server refuses the case otherwise.
* The patient is looked up in the chart and confirmed by a person, or chosen from the closest records. Where the fax
  and the record disagree on something the record holds (the name, the date of birth, the plan on file), both are
  shown.
* A field the fax does not give clearly is not guessed: the person asks the sending clinic, in a request Caduceus
  writes (naming the referral and the fields, never the patient) and the person sends. The fax waits until the
  clinic answers. A line not found at all is shown on the whole page instead of a crop.
* The cover sheet becomes page 1 of the case's packet, and the case says who checked the fax, when, and what they
  corrected.

The eval gate measures it against the expected value of every field on three faxes (clean, typical, degraded): no
wrong field may be accepted without a person. Today 0 of 27 are; the clean and typical faxes send no field to a
person, and the degraded one sends 8 of 9.
