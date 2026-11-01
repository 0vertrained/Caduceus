# Discovery memo: prior authorization at Mesa Verde Health

**Date:** October 31, 2026, end of discovery week 1
**For:** the practice administrator (sponsor), the IT lead and the security lead

> **A simulated engagement.** Mesa Verde Health is fictional. This memo, written for the Caduceus portfolio,
> records the discovery a real engagement would start with. People appear as roles. Mesa Verde figures come from
> the simulation and are marked *(simulated)*. Industry figures link to their sources.

**Recommendation.** Pilot Caduceus for 90 days at one clinic, on lumbar spine MRI requests to the two payers with
the most volume. Add knee replacement, then CPAP, only after the decision in weeks 11 to 13. Success means fewer
specialist minutes and clinician interruptions per case than the week 2 baseline. Two things must never happen: a
request sent without a person's approval, or a patient identifier reaching an outside model.

Lumbar MRI goes first: its criteria are clear, and it is usually high-volume, which week 2 will confirm. The manual
workflow stays in place, so the pilot can be switched off with nothing lost
([rollout plan](../rollout-plan.md)).

## Who we talked to

Mesa Verde is a six-clinic multi-specialty group with about 60 clinicians and a central prior-authorization (PA)
team of five *(simulated)*. In week 1 we met these roles.

| Role | Part in prior authorization | What they need from a pilot |
|---|---|---|
| Practice administrator (sponsor) | Owns the budget and the decision to expand | Change measured against a baseline; an off switch per clinic |
| PA manager | Leads the PA team; owns the workflow | Fewer minutes per case; picks the pilot clinic and team |
| PA specialists, 5 *(simulated)* | Read faxes, build and submit requests, chase notes, write appeals | Evidence beside each criterion; the final say on every send; readable screens |
| Ordering clinicians, about 60 *(simulated)* | Answer what the chart does not show; write notes when a payer asks | Fewer interruptions; questions on their phone |
| Compliance officer | Signs the HIPAA risk assessment | Proof that patient data stays inside; an audit trail they can verify |
| IT and security leads | Network, cloud account, sign-in, penetration test | No outbound route from the patient-data tier; the existing identity provider |
| EHR analyst | Grants API access; maps the referral work queue | Narrow, read-only access |
| Utilization-management (UM) nurse | Reviews payer criteria before use | Each criterion tied to its policy sentence; nothing runs unapproved |

The demo's personas include Dolores Montoya, a PA specialist, and Dr. Ana Reyes, an ordering clinician.

## How a referral moves today

1. A referral arrives by fax, as an EHR order or by email.
2. A PA specialist reads it, finds the patient in the EHR and types in the details.
3. The specialist checks whether the payer requires authorization, and under which policy.
4. The specialist reads the chart against each criterion: diagnosis, duration, treatment tried, imaging and tests.
5. If the chart does not answer a criterion, the specialist asks the ordering clinician and waits.
6. The specialist submits through a clearinghouse (X12 278) or the payer's portal.
7. The payer approves, pends with a question, or denies with a reason. Impacted payers must decide within 7
   calendar days, or 72 hours if expedited
   ([CMS fact sheet](https://www.cms.gov/newsroom/fact-sheets/cms-interoperability-and-prior-authorization-final-rule-cms-0057-f)).
8. A pend sends the specialist back to the clinician for a note. A denial needs an appeal, or the case stops.

Steps 5, 7 and 8 wait on someone else. None is timed yet.

| System | What it holds | How a pilot would reach it |
|---|---|---|
| EHR (Epic or Oracle Health, to confirm) | Record, visit notes, referral work queue | FHIR R4 API, read-only, through SMART Backend Services |
| Fax server (to confirm) | Faxed referrals | Its drop folder, read by OCR inside the network |
| Payer channels | Requests and decisions | A clearinghouse (X12 278) now; payer FHIR APIs as they go live |
| Identity provider | Staff accounts and groups | OIDC, with roles from its groups |

## What hurts, ranked

Ranked by the staff time each costs. The week 2 baseline may change the order.

| # | What hurts | Evidence | Not yet known here |
|---|---|---|---|
| 1 | Building each request by hand | About 350 requests a week for five specialists, about 70 each *(simulated)*. Practices average 40 requests per physician a week and 13 hours of physician and staff time ([AMA, 2026](https://www.ama-assn.org/press-center/ama-press-releases/ama-survey-prior-authorization-reform-pledge-falls-short-physicians)) | Minutes per case; volume by service line |
| 2 | Re-keying faxes | 89% of healthcare leaders say their organization still uses fax ([MGMA](https://mgma.com/data/data-stories/time-warp-the-lingering-legacy-of-fax-in-medical)). The 2025 CAQH Index puts fully electronic medical prior authorization at 40% ([AJMC, 2026](https://www.ajmc.com/view/caqh-index-finds-20-billion-in-cost-savings-opportunities)) | Share arriving by fax; fax quality |
| 3 | Interrupting clinicians | The AMA's 13 hours a week include physician time ([AMA, 2026](https://www.ama-assn.org/press-center/ama-press-releases/ama-survey-prior-authorization-reform-pledge-falls-short-physicians)) | Interruptions per case |
| 4 | Denials not appealed | 7.7% of nearly 53 million Medicare Advantage requests were denied in full or part in 2024. Only 11.5% of denials were appealed; 80.7% of appeals overturned the denial ([KFF, 2026](https://www.kff.org/medicare/issue-brief/medicare-advantage-insurers-made-nearly-53-million-prior-authorization-determinations-in-2024/)) | Denial and appeal rates |
| 5 | Delayed care | 95% of physicians say prior authorization delays care; 79% report patients abandoning treatment ([AMA, 2026](https://www.ama-assn.org/press-center/ama-press-releases/ama-survey-prior-authorization-reform-pledge-falls-short-physicians)) | Turnaround, referral to decision |

## Constraints

**Business associate agreements.** Mesa Verde is a HIPAA covered entity. Three agreements matter:

- The deployment team needs a BAA before handling any real case, including the week 2 timing.
- On AWS, patient data may go only to HIPAA-eligible services under the AWS BAA
  ([AWS HIPAA compliance](https://aws.amazon.com/compliance/hipaa-compliance)).
- The external model vendor gets no BAA, by design. It may receive only de-identified text, for drafting, under
  zero retention. Or that route stays off, which is the default.

**Patient data stays in Mesa Verde's VPC.** Work on the identifiable record runs on a model inside the network,
with no outbound route. If that model is down, the work waits; there is no fallback outside. What leaves:
requests, notes and appeals to the payer over its own channel, and de-identified drafting text if allowed.
De-identification has residual risk, such as a relative's name in a note. The
[data-handling note](../data-handling.md) sets this out.

**Security review.** Security reviews the threat model, the network design and a penetration test before
go-live. The compliance officer reviews data handling and vendor agreements, and signs the risk assessment.

**Staff comfort and accessibility.** The daily users include older office staff, and comfort with new tools
varies. Screens use 18 px text, a Larger setting, and status as an icon plus a word. Automated checks find 0 WCAG
2.2 AA violations on 50 screens. Keyboard, screen-reader, 200% zoom and real-user tests are not done yet. Training
is a 30-minute session around the larger text, a one-page desk guide, and daily office hours for two weeks.
Nothing is sent without a person, and every send can be undone for 30 seconds.

## Success metrics

Against the week 2 baseline, on the same case mix. Mesa Verde's own figures do not exist yet, so targets are
directions until they do.

| Metric | Baseline | Target | Counted from |
|---|---|---|---|
| Median specialist minutes per case | 50 cases timed in week 2 | Lower | Timed sessions, then the outcomes page |
| Clinician interruptions per case | Counted in week 2 | Lower; asked only when the record does not answer | Audit log |
| First-pass approval rate | Measured in week 2 | Higher, or at least not lower | Payer decisions |
| Payer questions and denials answered | Measured in week 2 | Each traceable to a note or appeal a person approved | Audit log |
| Decisions within CMS limits | Turnaround measured in week 2 | Tracked per payer; misses raised with the payer | Outcomes page |
| Criteria agreement with past decisions, shadow mode | None | At least 97% on 200 past cases | Eval gate |
| Unknown criteria reported as met | None | 0 | Eval gate |

**Stop at once if:**

- any identifier reaches the external model
- any request is sent without a recorded approval
- any wrong fax field is accepted without a person
- the audit chain fails verification

## Proposed pilot scope

The first deployment covers three service lines, with one procedure each to start.

| Service line | First procedure | Current criteria |
|---|---|---|
| Advanced imaging | Lumbar spine MRI (CPT 72148) | 4: back pain for 6 weeks; 6 weeks of conservative care; X-ray within 90 days; red flags ruled out |
| Orthopedic procedures | Total knee replacement (CPT 27447) | 5: osteoarthritis; X-ray narrowing; 12 weeks of non-surgical care; daily activities limited; no joint infection |
| Durable medical equipment | CPAP for sleep apnea (HCPCS E0601) | 4: diagnosis; sleep test within a year; severity; in-person evaluation before the test |

These sets are development paraphrases, tested on 11 synthetic cases (47 criteria), not any payer's text. On 54
harder synthetic cases with messy records, the engine agrees on 94% of criteria, and the gaps it found are listed in
the [hard evaluation](../hard-evaluation.md). In weeks 3 to 6 the UM nurse reviews each payer's real policy; only
reviewed sets are locked and run.

In scope: fax intake, the criteria check, the draft, clinician questions by phone, specialist approval, and payer
questions, denials and appeals. Still to build before live use: the Postgres case store, screen sign-in, the
in-VPC model, the three system links and the AWS environment.

Out of scope:

- CT, spinal injections and power mobility, until each has reviewed criteria.
- Cardiology and physical therapy requests, which the team also handles.
- Drug prior authorization, which the CMS rule also excludes.
- Expedited (72-hour) requests: not built yet, so they stay manual.
- Any judgment beyond the payer's published criteria. Caduceus never makes the decision.
- Notes to payers drafted by a model. Clinicians write their own.
- Free-form chat and bulk approval.

## Risks and open questions

To settle before scoping.

| # | Question or risk | Why it matters | Owner |
|---|---|---|---|
| 1 | Is a BAA with the deployment team signed before week 2? | The baseline uses real cases | Compliance officer, with the sponsor |
| 2 | Which EHR, and is its FHIR R4 API on? | No record access without it | EHR analyst |
| 3 | Which AWS account, and what GPU budget for the in-VPC model? | All patient-data work runs there | IT lead |
| 4 | Which fax server, and can it drop pages in a folder inside? | Intake reads from that folder | IT lead |
| 5 | Is the external drafting route allowed? | Draft quality against the simplest compliance answer | Compliance officer |
| 6 | Which clinic and which two payers? Payer mix is unknown | Defines the pilot | PA manager |
| 7 | Which clearinghouse and portals carry requests now? | Sets the submission path | PA manager, with the IT lead |
| 8 | Is a UM nurse available, and how many payer policies per service? | Payers can differ on the same service | PA manager |
| 9 | Staff do not trust or use it | No measured change | PA manager. In place: evidence for every "met", undo, training |
| 10 | A payer changes its policy quietly | Old criteria applied | UM nurse. In place: approval is blocked when the payer's version differs |
| 11 | De-identification misses a name in free text | It could reach an outside model | Compliance officer. In place: drafting only, from structured facts |

## Next steps

1. **Week 2:** once the BAA is signed, shadow three PA specialists for a day each and time 50 cases end to end.
2. **The IT lead and the EHR analyst** answer questions 2 to 4.
3. **The compliance officer** answers questions 1 and 5, after reading the data-handling note.
4. **The PA manager** answers questions 6 to 8.
5. **We write the scoping document:** problem, numbered assumptions, non-goals, definition of done with these
   metrics, risks and trade-offs. The sponsor and the PA team lead sign it before build work starts.
