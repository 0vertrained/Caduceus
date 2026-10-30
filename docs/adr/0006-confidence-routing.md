# 6. Route by confidence: low-confidence extraction goes to a person

Status: Proposed (the fax-intake slice it applies to is deferred)

## Context

Vision-language extraction from faxed referrals will be wrong sometimes, and wrong more often on degraded pages.
A wrong extracted field (a date of service, a diagnosis code) can turn into a wrong determination downstream.

## Decision (proposed)

Every extracted field carries a confidence and the page region it came from. Below a per-field threshold, the
field is shown to the PA specialist to confirm against the page image before the engine runs. Thresholds are set
from the held-out evaluation set, per quality tier (clean, typical, degraded), to hold field precision at the
target while measuring how many fields go to people.

## Consequences

* The determination is never built on an unconfirmed low-confidence field.
* The confirmation step costs specialist time; the case study reports the share of fields routed to people, so
  the trade-off is visible.
* Confidence calibration must be checked per model version; a model upgrade re-runs the threshold fit.
