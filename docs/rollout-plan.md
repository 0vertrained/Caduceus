# Rolling Caduceus out at a real health system

Mesa Verde Health is simulated. This is the plan for taking Caduceus to a real one: what changes, who has to say
yes, how the first 90 days run, how success is measured, and how it is switched off if it has to be. Every
figure below is one we would measure ourselves; none is assumed.

## What changes from the demo

| Demo | Production |
|---|---|
| Synthetic Synthea records in files | The EHR's FHIR R4 API (Epic or Oracle Health), read through SMART Backend Services with a scope limited to the resources the criteria need |
| A mock payer | A clearinghouse for X12 278 today. Each payer's FHIR prior authorization API as it goes live, which CMS-0057-F requires of Medicare Advantage, Medicaid and marketplace plans by January 1, 2027 |
| Faxes in a folder | The fax server's drop folder (RightFax, eFax Corporate or similar), still read by OCR inside the boundary |
| Development sign-in | The health system's identity provider over OIDC (built, and tested against a mock provider), with roles from its groups |
| A small in-VPC model on a CPU for the chart summary; drafts from a recorded or fixed-quota model | An in-VPC model for PHI work, and an external model for de-identified drafting, under a vendor agreement with zero data retention |
| SQLite in memory | Postgres on RDS, encrypted with KMS; the audit log archived under S3 Object Lock |

## Who has to say yes

- **The PA manager** owns the workflow and the success measures, and picks the pilot team.
- **The compliance officer** reviews `docs/data-handling.md`, the routing rule and the vendor agreements, and signs
  the HIPAA risk assessment.
- **A utilization-management nurse** reviews every criteria set before it is locked by hash. No policy runs
  without their approval.
- **An EHR analyst** grants the FHIR scopes and maps the referral work queue.
- **Security** reviews the threat model, the network design (no outbound route from the PHI tier) and the
  penetration test.

## The first 90 days

**Weeks 1 to 2: watch, then measure.** Shadow three PA specialists for a day each. Time 50 real cases end to end,
counting each person's minutes and every handoff. That gives the baseline: specialist minutes per case, how often
a clinician is interrupted, first-pass approval rate, denials, and turnaround. Pick one clinic, one service line
(lumbar MRI is a good first one: high volume, clear criteria) and the two payers with the most volume.

**Weeks 3 to 6: shadow mode.** Caduceus prepares every case in the pilot scope, but people work exactly as they do
today and never see its output. The eval gate runs against 200 past cases with known outcomes. The rule is that
criteria agreement is at least 97% and no unknown is ever reported as met. Fax OCR thresholds are fitted per
print quality on the clinic's own faxes, so that no wrong field is accepted without a person. Payer policies are
transcribed, reviewed by the UM nurse and locked.

**Weeks 7 to 10: live at one clinic.** The pilot team works in Caduceus, with:
- a 30-minute hands-on session, built around the larger-text option
- a one-page guide at each desk
- an hour of office hours a day in the first two weeks

Every send waits 30 seconds for an undo. Every clinician question goes to their phone. Every number on the
outcomes page is reviewed weekly with the PA manager.

**Weeks 11 to 13: decide.** Compare against the baseline. Expand to the next clinic or service line only if the
success measures below are met, with nothing on the stop list.

## How success is measured

Against the baseline from weeks 1 to 2, on the same case mix:

- Median specialist minutes per case: down.
- Clinician interruptions per case: down. The question goes only when the record does not answer it.
- First-pass approval rate: up, or at least not down.
- Payer questions and denials answered: each one traceable to a note or appeal a person approved.
- Decisions within the CMS limits: tracked per payer, so missed deadlines can be raised with the payer.

**Stop at once if** any identifier reaches the external model, any request is sent without a recorded
approval, any wrong fax field is accepted without a person, or the audit chain fails verification.

## What could go wrong, and what is already in place

| Risk | In place |
|---|---|
| A payer changes its policy quietly | The coverage-requirements check compares the payer's published version with the approved one and blocks approval on a mismatch |
| A model update changes behaviour | The eval gate runs on every change, including a model change; thresholds are re-fitted per model version |
| Staff do not trust it | Every "met" links to the sentence and page it came from; nothing is sent without a person; everything can be undone for 30 seconds |
| Bad fax quality | Confidence routing sends unsure fields to a person, with the part of the page beside them |
| The in-VPC model is down | PHI work waits and drafts fall back to the template; nothing is routed outside |
| An injected instruction in a clinical note | Tools are allowed per workflow step, and only the agent runtime can write; the red-team suite runs in CI |

## Switching it off

Each clinic has its own switch. Turning it off stops new preparation, pauses the outbox (nothing approved but
unsent is lost: it waits in the outbox), and leaves the existing manual workflow untouched, because Caduceus never
replaced it during the pilot. The audit log stays, for the record.
