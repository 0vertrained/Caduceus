# Pilot scope: Caduceus at Mesa Verde Health

**Date:** October 31, 2026, end of scoping
**For sign-off by:** the practice administrator (sponsor), the PA manager, the compliance officer and the IT lead
**Follows:** the [discovery memo](discovery-memo.md), which set the direction; this sets what is delivered, how it
is accepted, and what it depends on

> **A simulated engagement.** Mesa Verde Health is fictional and its people appear as roles. Everything listed as
> built can be checked in this repository and in the public demo; everything else is work this scope commits to.

## What the pilot is for

To find out, on one clinic's real referrals, whether Caduceus cuts the minutes a prior authorization specialist
spends per case and the times a clinician is interrupted, without ever sending a request no person approved and
without a patient identifier reaching a model outside Mesa Verde's network.

## Scope

**Service lines.** Lumbar spine MRI (CPT 72148) for the first six weeks of live use, then total knee replacement
(CPT 27447) and CPAP for sleep apnea (HCPCS E0601) if the week-11 decision says so.

**Where.** One clinic, the two payers with the most volume there, and the specialists who work its queue.

**Steps covered.** Fax intake read inside the network; matching the patient; the criteria check against the
approved policy version; showing where in the packet an unanswered criterion may be answered; the clinician's
attestation by phone; drafting the request; a specialist's approval, with 30 seconds to undo; sending; the payer's
review, questions, denials and appeals; expedited requests on the 72-hour clock.

**Out of scope.** Other service lines; drug prior authorization; any decision beyond the payer's published
criteria (Caduceus never decides, and never sends without approval); model-drafted notes to payers (clinicians
write their own); bulk approval; free-form chat.

## What exists and what the pilot adds

| Capability | Built and tested now | The pilot adds |
|---|---|---|
| Criteria from the payer's own words | Knee and CPAP compiled from Medicare's LCD and NCD text, approved by a reviewer, locked by hash; lumbar MRI from development criteria | The two payers' own policies, reviewed by Mesa Verde's UM nurse |
| Patient records | FHIR R4 bundles on disk, or a HAPI FHIR server; SMART on FHIR launch in development | The EHR's FHIR API, read-only, scoped to the resources the criteria use |
| Fax intake | OCR inside the boundary, with every unsure field sent to a person | The fax server's drop folder; OCR thresholds fitted to the clinic's faxes |
| Models | A recorded or fixed-quota external model for de-identified drafting; an administrator's off switch; in-network model in progress | An in-network model for work that needs the patient's details, and a vendor agreement with zero retention for the external route |
| Payer | A simulated payer with review times, questions, denials, appeals and expedited handling | The clearinghouse (X12 278) and each payer's FHIR prior authorization API as it goes live |
| Cases and audit log | SQLite, or Postgres with an append-only audit table | Postgres on RDS, encrypted, the audit log archived under object lock |
| Sign-in | Development and demo sign-in; OIDC on the API side, with sign-in screens in progress | Mesa Verde's identity provider, roles from its groups |

## Deliverables and how each is accepted

| # | Deliverable | Accepted when |
|---|---|---|
| 1 | Reviewed criteria for lumbar MRI from both payers | The UM nurse approves each version on the [policies page](../admin-guide.md#coverage-policies); every policy sentence is quoted word for word from the payer's document |
| 2 | Shadow-mode run on 200 past cases with known outcomes | Criteria agreement at least 97%; no criterion the record cannot answer reported as met; no wrong fax field accepted without a person (the [eval gate](../../evals)) |
| 3 | The integration with the EHR, fax server and clearinghouse | Each runs end to end on test data in Mesa Verde's environment, with the network allowing no outbound route from the patient-data tier |
| 4 | Sign-in through Mesa Verde's identity provider | Each role sees only what it may; a person without a role cannot sign in; the IdP's groups map to roles as the [admin guide](../admin-guide.md#roles) sets out |
| 5 | The usability study | Five specialists and three clinicians complete the [test kit](../research/usability-test-kit.md) tasks; every severity-3 or -4 finding is fixed before live use |
| 6 | Operations handover | The IT lead runs each procedure in the [runbook](../runbook.md) once, including switching off the external model and verifying the audit log |
| 7 | The week-11 readout | Specialist minutes per case, clinician interruptions per case, first-pass approval, denials and turnaround, against the week-2 baseline |

## Timeline

| Weeks | Phase | Ends when |
|---|---|---|
| 1 to 2 | Baseline: shadow three specialists, time 50 cases end to end | The baseline is agreed with the PA manager |
| 3 to 6 | Shadow mode: Caduceus prepares every in-scope case; people work as before and never see its output | Deliverables 1 to 4 accepted |
| 7 | Usability study and fixes | Deliverable 5 accepted |
| 8 to 10 | Assisted: specialists work from Caduceus's prepared cases, approving each request themselves | Two weeks with no safety event (an unapproved send, or an identifier sent outside) |
| 11 | Readout and decision | The sponsor decides: stop, continue, or add knee and CPAP |
| 12 to 13 | Handover, or a clean stop | Deliverable 6 accepted, or the pilot switched off with the manual workflow untouched |

## Who does what

| | Sponsor | PA manager | Compliance | IT lead | EHR analyst | UM nurse | Deployment team |
|---|---|---|---|---|---|---|---|
| Baseline and success measures | Approves | Owns | | | | | Measures |
| BAA and risk assessment | Signs | | Owns | | | | Supports |
| Criteria versions | | Consulted | | | | Approves each | Compiles |
| Integrations and network | | | Consulted | Owns | Owns the FHIR scopes | | Builds |
| Usability study | | Recruits | | | | | Runs |
| Go, stop or extend | Decides | Recommends | Can stop it | Can stop it | | | Reports |

## Assumptions

- A BAA covers the deployment team before week 2, when real cases are first timed.
- The EHR exposes FHIR R4, and the analyst can grant read scopes for Patient, Condition, Procedure, Observation and
  DocumentReference.
- Mesa Verde provides an AWS account, and a budget for the in-network model: a GPU instance for a 7 to 8 billion
  parameter model, or a CPU instance for a smaller one, sized during weeks 3 to 6.
- The fax server can drop pages into a folder inside the network.
- Compliance decides in week 3 whether the external drafting route may be used. If not, drafting runs in the
  network, or from the template, and nothing else changes.

## Changes

A new service line, payer or policy version is a change: the UM nurse reviews and approves the criteria, and the
eval gate runs on them before they go live. Anything that widens where patient information can go needs the
compliance officer's written approval first.

## Risks

| Risk | What is in place | Owner |
|---|---|---|
| Specialists do not trust or use it | Evidence for every "met", the passage for every unanswered criterion, 30-second undo, the usability study | PA manager |
| A payer changes its policy quietly | The payer's published version is checked at intake, and approval is blocked when it differs from the approved one | UM nurse |
| De-identification misses an identifier in free text | Drafts are written from structured facts; every request is checked for the patient's own identifiers before it leaves; the route can be switched off | Compliance officer |
| The in-network model is slow on CPU | Work that needs it runs in the background, and the template is the fallback; nothing waits on a model | IT lead |
| An integration slips | Shadow mode does not need the clearinghouse; the pilot can start assisted use on one payer | Deployment team |

## Sign-off

| Role | Name | Date |
|---|---|---|
| Practice administrator (sponsor) | | |
| PA manager | | |
| Compliance officer | | |
| IT lead | | |
