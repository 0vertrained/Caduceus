# Which model should write the drafts?

**Date:** October 30, 2026
**Decision:** keep Kimi K2.6. Add a check, for any model, that rejects a draft saying every criterion is met when
one is not.
**Data:** [evals/bakeoff/results.json](../evals/bakeoff/results.json) holds every request's reply, time, rule
scores and judge verdict. [evals/bakeoff/summary.md](../evals/bakeoff/summary.md) is the table below.

## The question

The demo picked Kimi K2.6 from a ranked list of models offered by Ollama Cloud, mostly on reputation. That choice
should rest on the work the model will do here: turning de-identified case facts into the paragraph a payer reads.
The bake-off gives every candidate the same requests and checks the replies the same way.

## Setup

- **Requests.** 17, captured by running the demo's story with a stub model (`scripts/bakeoff.py capture`): 16
  justification paragraphs and 1 appeal, from 11 synthetic cases across three services (lumbar MRI, knee
  replacement, CPAP). They are exactly what the router lets leave: de-identified, with placeholders such as
  `[NAME_1]`. No other text was sent.
- **Instructions.** The product's own. Use only the facts. Add no diagnoses, dates, results or opinions. Keep
  every placeholder. Say plainly when a criterion is awaiting clinician attestation. Write 90 to 140 words (120 to
  180 for an appeal, which must also quote the payer's reason and the policy sentence exactly).
- **Contestants.** Kimi K2.6 and gpt-oss 120B. DeepSeek V3.2 and GLM-5 were also on the list, but Ollama Cloud
  does not offer them under those names, so they did not run.
- **Settings.** Thinking turned off, a 300-second timeout, up to three tries per request.
- **Checks.** Deterministic rules, then a judge model that is not a contestant (Mistral Large 3, 675B). The judge
  reads the facts and the reply, lists any claim the facts do not support, and rates clarity from 1 to 5.
- **Run.** [`model-bakeoff`](../.github/workflows/model-bakeoff.yml), run 41163393616 on commit `c6d6791`, using
  the demo's key from Secret Manager. A job that can only push wrote the results to the `bakeoff-results` branch.

## Results

| | Kimi K2.6 | gpt-oss 120B |
|---|---|---|
| Answered | 17 of 17 | 17 of 17 |
| Median seconds per reply (slowest) | 7.4 (11.0) | 2.6 (3.8) |
| Length within the instructions | 13 of 17 | 17 of 17 |
| Placeholders invented | 0 | 0 |
| Numbers not in the facts | 0 | 0 |
| Pending attestation stated, when there was one | 5 of 5 | 5 of 5 |
| Says every criterion is met when one is not | 0 | 1 |
| Appeal quotes the reason and the policy | yes | yes |
| Judge: replies with an unsupported claim | 8 of 16 (1 verdict unreadable) | 12 of 17 |
| Judge: clarity | 4.94 | 4.88 |

Kimi's four length misses were all short: 76 to 84 words against a floor of 90. gpt-oss reported a median of
1,086 output tokens for paragraphs of about 120 words (Kimi: 156), so it probably still reasons with thinking
turned off. On a fixed-quota plan that costs only time, and it was still the faster model.

## Reading every reply

The judge flagged 20 replies, so I read all 34 against their facts, flagged or not.

| | Kimi K2.6 | gpt-oss 120B |
|---|---|---|
| A claim that misstates the case | 2 | 2 |
| A minor overstatement | 0 | 4 |
| Accurate | 15 | 11 |

**Kimi K2.6, misstatements.** Both follow one pattern. When the facts mark a criterion *not met*, Kimi says so
correctly, then invents a next step: "Clinician attestation is needed to address whether this active infection has
been resolved" (r09, an infected knee) and "Whether this criterion may be satisfied by clinician attestation is
pending review" (r10, an X-ray six months old). Nothing in the facts says attestation applies to either.

**gpt-oss 120B, misstatements.**
- r06 opens "meets the documented criteria for total knee replacement" while two criteria await attestation. It
  says so later in the same paragraph, but a reviewer reads the first sentence first.
- r09: "She has completed twelve weeks of conservative management". The record shows one corticosteroid injection,
  three months ago. That meets the criterion under the policy, but no twelve weeks of anything is documented.

**gpt-oss 120B, minor overstatements.** "Twelve weeks" of physical therapy where the note says thirteen (r02). A
"polysomnographic study" where the facts say "sleep test" (r13). Conservative treatment "documented as completed"
where it was attested, not documented (r15). An evaluation "confirming obstructive sleep apnea" where the
evaluation recorded symptoms (r17). It also tends to end with an opinion the instructions rule out ("is medically
necessary", "supporting authorization").

Both appeals quote the denial reason and the policy sentence exactly and argue the policy's second pathway (an
index between 5 and 14 with hypertension or ischemic heart disease), which is the right argument.

## How far to trust the judge

Of the judge's 20 flags, 4 were misstatements and 3 were minor overstatements. The other 13 were wrong: it flagged
true statements ("The remaining criteria are met"), facts copied from the record, and replies for leaving things
out. It missed one minor overstatement among the replies it passed. A judge right about a third of the time is
useful for pointing a person at replies to read, not for ranking models. The flag counts in the results table should
not be read as a quality score.

The deterministic rules had a bug of their own. gpt-oss writes non-breaking hyphens ("apnea‑hypopnea") and curly
quotes, so the first scoring said its appeal quoted neither the reason nor the policy. It quoted both. The rules now
compare plain punctuation, a test covers it, and the published results were rescored from the same replies.

## Decision

**Keep Kimi K2.6.** On the same requests it had 2 problems against 6, none of them overstatements, wrote slightly
clearer prose, and used only plain ASCII. gpt-oss used seven typographic characters, including "≥" and narrow
no-break spaces. The costs:

- **Speed.** 7.4 seconds a reply against 2.6. The slowest, 11.0, is inside the demo's 20-second limit, and the
  demo replays recorded replies anyway.
- **Length.** A quarter of its drafts run 6 to 14 words short. A short, accurate paragraph is the better failure.

gpt-oss 120B is the fallback if speed matters more, for example drafting live in a clinic's working day. It stayed
within length every time. Before choosing it, check how its typographic punctuation renders in each payer's portal.

## What changed in the product

The most dangerous error in the run, saying every criterion is met when one is not, is now blocked for every model.
`contradicts()` in `src/caduceus/drafting.py` checks each justification draft against the facts it was written
from. If any criterion is awaiting attestation or not met, a draft claiming that all are met is rejected. The case
falls back to the template, and the reason ("Rejected: the draft said every criterion is met, but the facts list one
that is not") is kept with the draft. The check is deterministic, so it runs on every draft, not on a sample.
Replaying the demo's story against its 23 recorded Kimi replies, every draft and the appeal still come from the
model: the check rejects none of them.

## Gaps

- **Two contestants, not four.** DeepSeek V3.2 and GLM-5 need their Ollama Cloud names (from `/api/tags`) before
  they can run.
- **One run, 17 requests, three services.** Replies vary between runs, and a second run would show by how much.
  Changing the bake-off script reruns it on `main`, and the `bakeoff-results` branch keeps the latest run. The
  numbers above are from run 41163393616, committed here.
- **One human reader.** The hand review is mine. A second reviewer, ideally a utilization-review nurse, should read
  the same 34 replies.
- **Kimi's pattern is not caught yet.** No rule detects an invented attestation step for a criterion that is not
  met. Next: say in the instructions that a criterion marked not met is not awaiting attestation, add a rule for it,
  and rerun. Changing the instructions changes every request, so the deploy would record all replies again.

## Running it again

```
python scripts/bakeoff.py capture evals/bakeoff/requests.json    # after the demo's story or prompts change
```

Then push to `main`, or run the `model-bakeoff` workflow by hand. The script needs the Ollama key, which only the
workflow can read.
