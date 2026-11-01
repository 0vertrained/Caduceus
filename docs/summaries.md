# The chart summary, and the check on it

**Date:** November 1, 2026
**Result:** the in-network model (Qwen2.5-1.5B-Instruct on a CPU) wrote 188 summary sentences for 66 cases. On 34
held-out cases, the check replaced 6 of 90 sentences: 3 said more than the record, including one that called an
apnea-hypopnea index of 15 "severe" sleep apnea, and 3 were faithful wording it could not tell apart. Of the 84 it
kept, a reading found 1 misleading. It takes about 9 seconds a summary.
**Report:** [first run](../evals/summaries/first-run.json), committed as it came out, from the
[summaries](../.github/workflows/summaries.yml) workflow.

## What the summary is

When Caduceus asks the ordering clinician to attest, the screen first says what the chart shows. Caduceus shows
the record's own facts at once, and the model inside the network replaces them with a readable summary when it
arrives. It is the only model allowed to see who the patient is, so it runs on Mesa Verde's side, on a CPU, in the
background.

## How it is written

- The model gets one block per criterion the chart documents: the criterion, the engine's finding and up to two
  source sentences, keyed by the criterion's id. It answers with one sentence per id, and the server holds it to
  that JSON shape, so there is no place for a claim about anything else.
- What the clinician is asked about, which the chart does not answer, is never the model's to say. The engine
  adds it word for word: "The chart does not say whether this was done: …".

## How it is checked

Each sentence is read against its own criterion's facts ([src/caduceus/grounding.py](../src/caduceus/grounding.py)):

- **Numbers.** Every number and date in it must be in the facts.
- **Negations.** Whatever it negates, the facts must negate too ("without improvement" fails against "minimal
  improvement"), and it may not state what the facts only ever negate ("an acute fracture" fails against "no acute
  fracture"). "Ruled out", "negative" and "absence of" count as negations.
- **Terms.** Every other word must be in the facts, apart from grammar and the vocabulary of reporting
  ("conducted", "as evidenced by", "the most recent").

A sentence that fails is replaced with the engine's words for that criterion. If every sentence fails, or the
reply is not the JSON asked for, the record's facts stand, and the page says why. The audit log and the case
history record each replacement by criterion and reason, never the text.

## What the measurements found

| | Summaries | Sentences | Replaced | Of those, said more than the record | Kept, but misleading |
|---|---|---|---|---|---|
| First check, dataset and dev | 32 | 98 | 27 | 3 | 0 |
| Check tuned on dev, same sentences | 32 | 98 | 3 | 3 | 0 |
| Held-out, first run | 34 | 90 | 6 | 3 | 1 |

Every sentence was read by hand against its facts.

- **The first check was too strict.** It took the vocabulary of reporting for claims: "conducted", "as evidenced
  by", "the most recent", and a negative screen phrased differently from the record. The tuned check compares that
  vocabulary by stem, reads results such as "screen: negative" as negations, and ends a negation's scope at "but"
  and the like. That was the only tuning, and it was done on the dev split.
- **What it catches** on held-out cases: an index of 15 called "severe" (15 to 29 is moderate); "severe" again for
  an index of 42, correct but not in the facts; and an elaboration ("a history of conservative management for her
  knee condition"). On the dev split: an invented reason ("a higher risk of adverse events"), a number from another
  criterion, and a diagnosis detail that criterion's facts lack.
- **What it cannot catch.** One held-out sentence passed with its meaning turned by syntax: "The patient's chart
  does not document any red-flag symptoms that were ruled out by the clinician." Every word is in the facts, and the
  negation governs words the facts negate too. A lexical check cannot read that; an entailment model or a larger
  in-network model as a second reader could.

After the held-out run, "starting from" joined the reporting vocabulary and "absence of" the negations. With those
two fixes the same held-out sentences give 3 replacements, all real, but that is no longer a held-out result.

## Running it

`python -m caduceus.evals.summaries --sets dataset,dev,heldout --report <file>` with `CADUCEUS_IN_VPC_URL` and
`CADUCEUS_IN_VPC_MODEL` set. In CI the [summaries](../.github/workflows/summaries.yml) workflow runs the published
model image on the runner's loopback address whenever the summary, the check or the model changes, and pushes the
report to the `summaries-results` branch.
