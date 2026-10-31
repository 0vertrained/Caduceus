# Case study: a prior-authorization agent that keeps patient data inside

**Date:** October 31, 2026
**Live demo:** [caduceusdemo.netlify.app](https://caduceusdemo.netlify.app/), with a
[98-second walkthrough](https://caduceusdemo.netlify.app/demo.mp4)
**Customer:** Mesa Verde Health, a simulated six-clinic group. Everything about it is fictional, and every patient
is synthetic. The [discovery memo](discovery-memo.md) sets out the engagement.

Caduceus prepares prior-authorization requests for a medical group. It reads a faxed referral and checks the
patient's record against the payer's criteria. It asks the ordering clinician only what the record cannot
answer, drafts the request, and sends nothing until a specialist approves. Patient data stays inside the
customer's network. A deterministic engine, not a model, decides whether each criterion is met, and every
decision is in a hash-chained audit log. On 47 criteria built to known labels, the engine agrees 100% of the time.
On 156 held-out criteria from messy records, the engine as shipped agreed 88% of the time and made 4 unsafe errors.
That is the more useful number, and most of this document is about what was learned from it.

## The problem

Prior authorization is the payer's check that a service meets its coverage criteria before it is done. At a group
like Mesa Verde, five specialists handle about 350 requests a week *(simulated)*, arriving by fax, EHR order or email.
Each one means reading the chart against every criterion and asking a clinician what the chart does not say. Each request goes out
by portal or clearinghouse, and pends and denials come back for more work. Nationally, practices report 40 requests
per physician a week and 13 hours of staff and physician time
([AMA, 2026](https://www.ama-assn.org/press-center/ama-press-releases/ama-survey-prior-authorization-reform-pledge-falls-short-physicians)).
In Medicare Advantage, 11.5% of denials are appealed, and 80.7% of appeals win
([KFF, 2026](https://www.kff.org/medicare/issue-brief/medicare-advantage-insurers-made-nearly-53-million-prior-authorization-determinations-in-2024/)).

Four constraints shaped the design:

- **Patient data may not leave.** By design there is no BAA with a model vendor, so identifiable work needs a
  model inside the customer's network.
- **Every decision must be explainable.** A payer, an auditor or a clinician may ask why a criterion was met.
- **The users are not technologists.** Many PA staff are older office workers, so the interface has to be plain,
  large and forgiving.
- **The rules are moving.** CMS-0057-F requires payer APIs, specific denial reasons and a 7-day decision clock.

## What was built

A referral moves through these steps:

1. **Fax intake.** OCR runs inside the container. Every field has a confidence and its region on the page, and
   unsure fields go to a person, who checks each one against the image.
2. **The record check.** A deterministic engine evaluates the patient's FHIR record against an approved criteria
   set, locked by hash. It answers met, unmet or unknown, with the sentence behind each. Missing evidence is
   never "met".
3. **The clinician's attestation.** Only the unknowns go to the ordering clinician, as plain questions on a phone.
4. **The draft.** A model writes the justification from de-identified facts. The interface shows exactly what was
   sent to it.
5. **Approval.** A specialist approves. The request then waits 30 seconds in an outbox, where it can be undone, and
   can be recalled until it is sent.
6. **The payer.** A coverage-requirements check at intake blocks approval if the payer's policy has changed. The
   request carries its documentation answers. A payer question goes back to the clinician for a signed note, and a
   denial gets an appeal drafted from the policy, which a person approves.
7. **The record of it all.** Every step is in an append-only, hash-chained log. An outcomes page counts the work
   from that log.

The decisions behind it:

| Decision | Why | What it costs |
|---|---|---|
| Route model calls by whether they carry PHI, enforced in types ([ADR 1](../adr/0001-phi-aware-routing.md)) | Identifiable text can reach only the in-VPC model, with no fallback. The external model takes only HMAC-stamped, de-identified text, re-scanned before it leaves | Drafting quality depends on de-identified facts; work waits if the in-VPC model is down |
| A deterministic engine decides; models only write prose ([ADR 2](../adr/0002-policy-as-code.md)) | Reproducible and auditable, and an unknown is never guessed | Criteria must be compiled and reviewed by a person; the engine is only as good as that compilation |
| Customer systems as MCP servers, with role and step checks on the server ([ADR 3](../adr/0003-mcp-integration-boundary.md)) | An instruction injected into a note cannot make the agent submit or browse outside its step | Every tool needs an allow-list entry |
| A database state machine with an outbox, Temporal later ([ADR 4](../adr/0004-state-machine-then-temporal.md)) | Exactly-once sending and undo, without new infrastructure | Timers and retries are hand-written |
| Low-confidence extraction goes to a person ([ADR 6](../adr/0006-confidence-routing.md)) | A wrong fax field is worse than a slow one | Specialists check some fields by hand |
| A public demo that holds only a fixed-quota model key | Anyone can use it, and no one can run up a bill | Replies are recorded at deploy time, and live calls time out at 20 seconds |

## How it is checked

| What | Result |
|---|---|
| Eval gate, every push: criteria agreement on 11 constructed cases | 1.00 on 47 criteria; 0 unknowns reported as met |
| Identifiers reaching the external model | 0 of 181 planted |
| Red-team attacks: note injection, tool outside its step, send without approval, forged stamp | 0 of 4 succeed |
| Wrong fax fields accepted without a person | 0 of 27, across clean, typical and degraded faxes |
| [Hard sets](../hard-evaluation.md), held-out first run: engine agreement on messy records | 0.885 on 156 criteria; 4 unsafe errors |
| The same, after fixes (no longer held out) | 0.942; 2 unsafe errors, both needing a criteria change |
| Packet retrieval, held-out: the evidence in the top 5 passages | 0.899 (plain BM25: 0.512); text-only evidence 14 of 15 |
| [Model bake-off](../model-bakeoff.md): replies with a problem, read by hand | Kimi K2.6 2 of 17; gpt-oss 120B 6 of 17 (2 misstatements, 4 overstatements) |
| Accessibility, axe-core WCAG 2.2 AA | 0 violations on 32 screens; no sideways scroll at 320 px |
| Known vulnerabilities | 0 in Python and npm packages; 2 medium in CPython with no fix released |
| Tests | 180 |

Every number here comes from synthetic data, and none is a claim of clinical or financial impact. Synthetic
records cannot stand in for the distribution of real ones, as Synthea's own documentation warns. That is why the
rollout plan starts with shadow mode on real cases before anyone relies on these results.

## What went wrong, and what changed

**The first deploy with a model key failed at startup.** Seeding the demo drafted every case live, and the chosen
model reasons before it answers, so startup ran past Cloud Run's limit. Cloud Run kept serving the old revision, so
no visitor saw it. A cold start now uses only recorded replies, live calls time out at 20 seconds, and thinking is
off for prose. ([Postmortem](../incidents/2026-10-30-deploy-failed-at-startup.md).)

**De-identification removed ordinary words.** A pattern for policy numbers read the words "policy sentence" as one,
and the model repeated the placeholder into an appeal letter. The leakage suite only tested under-removal. A record
number must now contain a digit, and a test fails if any fixed prompt text is removed.
([Postmortem](../incidents/2026-10-30-deid-false-positive.md).)

**The model judge was wrong more often than right.** The bake-off's judge model flagged 20 replies. A hand read of
all 34 showed 7 of those flags were right. The scoring had a bug too: typographic hyphens hid a faithful quote. The
worst real error, a draft claiming every criterion was met while two were pending, is now rejected for every
model by a deterministic check.

**The 100% number did not survive messy records.** The original 11 cases were built to known labels with clean,
short packets. A held-out set of 36 cases had diagnoses entered in error, therapy still in progress, codes outside
the value sets and osteoarthritis in the other knee. On those, the engine agreed 88% of the time, with 4 unsafe
errors. Two of the three engine fixes were named from the dev split before the held-out run. The third was found
on it and is labelled so. The two unsafe errors left are not engine bugs. "The knee to be replaced" was compiled
as "a knee", and changing an approved criteria set is a reviewer's decision, so the change is proposed, not made.

**Retrieval generalized less well than it looked.** On the dev split, the evidence was the first passage 83% of the
time. On held-out phrasing it was first 50% of the time and in the top 5 90% of the time. Plain BM25 fell to 51%,
so the clinical vocabulary, written before any packet existed, is what holds it up. Retrieval was not tuned after
the held-out run.

## What was cut, and why

The plan called for 2,000 synthetic patients and 1,200 referral packets. That scale exists to measure extraction,
OCR across scan quality, and retrieval over long packets. The first slice had none of those parts to measure, so it
used 11 constructed cases, each built to exercise one path. When retrieval was built, 54 harder cases came with it,
built to test it. A 300-packet hand-verified set needs a utilization-review nurse, not a generator.

Also not built:

- A model inside the network (the demo has none, so its identifiable route stays unused).
- Dense retrieval, which needs an in-VPC embedding model.
- The Postgres case store, then Temporal.
- OIDC sign-in in the interface; the API side is done.
- The AWS deployment: the Terraform passes Checkov but has not been applied.
- Real payer policy text from the Medicare Coverage Database.
- Expedited requests.
- Usability testing with real PA staff.

The README's [Not built yet](../../README.md#not-built-yet) section tracks the full list.

## Next

1. **Criteria changes, for a reviewer.** Laterality in the knee criteria, "before the test" for the sleep
   evaluation, and five value-set additions.
2. **Retrieval in the interface.** Show the top passages beside an unanswered criterion, so the clinician sees
   where to look before attesting.
3. **Real data under a BAA.** De-identified real packets and a UM nurse's review of the criteria. This is the
   shadow-mode phase of the [rollout plan](../rollout-plan.md), where the synthetic numbers are tested against
   real ones.
4. **Production shape.** A second held-out split, a CI gate of no unsafe errors, then Postgres, sign-in and the AWS
   apply.
