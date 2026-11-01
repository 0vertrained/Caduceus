# The hard sets: messy records and long packets

**Date:** October 31, 2026
**Result:** on 36 held-out cases, the engine as shipped agreed with the labels on 88% of 156 criteria, not 100%,
and made 4 unsafe errors. Retrieval found the evidence in the top 5 passages 90% of the time, against 51% for plain
BM25. Engine fixes planned from the dev split, plus one found on the held-out run, raise agreement to 94% and
leave 2 unsafe errors, both needing a reviewed change to the knee criteria.
**Data:** [data/hard](../data/hard), built by [scripts/build_hard_set.py](../scripts/build_hard_set.py).
**Reports:** [first held-out run](../evals/hard/heldout-first-run.json), committed as it came out, and
[after fixes](../evals/hard/heldout-after-fixes.json). CI reports both splits on every push.
**Updated:** October 31, 2026, evening: retrieval changed after the held-out run (see [Changes since the held-out
run](#changes-since-the-held-out-run)), and the "where to look" list the product shows is now measured.

## Why

The original eval set has 11 cases with short, clean packets, and the engine agrees with its 47 labels 100% of the
time. That number says the engine does what its criteria say on the data it was built with. It does not say how
the engine behaves on the records a health system actually has, and nothing found evidence buried in a long
packet. These sets test both.

## The sets

| | Dev | Held-out |
|---|---|---|
| Cases (lumbar MRI, knee replacement, CPAP) | 18 (6 each) | 36 (12 each) |
| Criteria labelled | 78 | 156 |
| Packet pages | 200 | 384 |
| Retrieval queries (criteria with text in the packet) | 64 | 129 |
| Used for | developing retrieval, choosing fixes | one run, never tuned against |

Each criterion in a case gets a state from a fixed list. The state sets the structured FHIR record, the packet
sentences, and the expected answer. Answers are written from the approved criteria set and its policy sentence,
never by running the engine. Text that is only in the packet does not make a criterion met: the engine reads the
structured record, and text-only evidence is what retrieval and the clinician's attestation are for.

Most states are ordinary. The others are problems found in real charts:

- a diagnosis refuted, or entered in error;
- a code outside the value set: the retired M54.5, M17.9, 72114, 95811, I11.9;
- therapy still in progress, with no end date;
- osteoarthritis in the knee that is not being replaced;
- a sleep evaluation dated after the sleep test;
- a chest film where a lumbar one was needed;
- a back pain diagnosis with no onset date, only the date it was charted;
- a newer red-flag screen that reverses an older one;
- dates exactly on the edge of a window (41 and 42 days, 90 and 91, 365 and 366).

Packets run 7 to 14 pages in shuffled order, with copied-forward boilerplate, near-miss distractors (a chest X-ray,
physical therapy for a shoulder, an influenza injection) and OCR damage on a quarter of pages. Every sentence that
bears on a criterion is recorded with its page and character offsets. Rebuilds are byte-identical.

## Protocol

The order is in the commit history:

1. Retrieval and its clinical vocabulary were committed (`423504e`) before any packet text existed.
2. The generator and both splits were committed (`491c5e4`) before either was evaluated. Held-out packets use
   phrasings the dev split never has.
3. Retrieval was tuned on the dev split only (`91aaba4`).
4. The held-out split was run once and committed as it came out (`2f43ed0`). That commit names the engine fixes
   planned from the dev run beforehand.
5. The fixes were made (`c2d3429`). One more was found on the held-out run and is labelled so. Held-out figures
   after this point are no longer held-out results.

Retrieval was changed after the held-out run, for the "where to look" list. Those changes and what they cost on
the held-out split are under [Changes since the held-out run](#changes-since-the-held-out-run).

## Engine

| | Held-out, first run | After fixes |
|---|---|---|
| Agreement | 0.885 (138 of 156) | 0.942 (147 of 156) |
| Unsafe: says met when the answer is unmet or unknown | 4 | 2 |
| Wrong block: says unmet when the answer is met or unknown | 6 | 0 |
| Needless question: says unknown when the answer is met | 8 | 7 |

The dev split after fixes: 0.949, with 3 unsafe errors and 1 needless question.

What failed, and what changed:

| Record shape | Held-out errors | Cost | Change |
|---|---|---|---|
| Diagnosis entered in error or refuted, counted as real | 7 | 2 unsafe, 5 wrong blocks | Fixed: a voided diagnosis is not the patient's (planned from dev) |
| Therapy in progress, read as zero days | 1 | needless question | Fixed: it counts up to the as-of date (planned from dev) |
| No onset date, so the charting date was read as the onset | 1 | wrong block | Fixed: the charting date is a lower bound, so the answer is unknown, not unmet (found on held-out) |
| Osteoarthritis in the other knee | 2 | unsafe | Not fixed: the knee criteria have no laterality |
| Valid code outside the value set | 7 | needless question | Not fixed: expanding a value set is a reviewer's decision |

The two not fixed are not engine bugs. The criteria sets do not say what the policy sentence says:

- **Laterality.** "Osteoarthritis of the knee to be replaced" was compiled as "osteoarthritis of a knee". The fix is
  a side on each code in the value set and the requested side on the case, so a diagnosis on the other side does
  not count. Until then the reviewer sees "left knee" in the evidence, but the engine says met.
- **Order.** "A clinician evaluated the patient in person before the sleep test" was compiled without "before".
  The dev split found one evaluation dated after the test, which the engine accepts. The fix is a rule that the
  evaluation precedes the qualifying test.
- **Value sets.** 95811, 72114, M54.5, M17.9 and I11.9 each mean what their criterion asks for. Each costs a
  clinician a needless question, not a wrong approval.

Each is a new version of an approved criteria set, so a reviewer approves it under ADR 2, and the payer's policy
hash changes with it. They were proposed here, then made: the knee and CPAP sets were compiled again from
Medicare's own text (LCD L36007 and LCD L33718 with NCD 240.4) as versions 2026-11, with the request's side on the
knee criterion and the evaluation-before-test order on the CPAP one, and approved by a reviewer. The hard sets are
labelled for the development versions, which the new ones change in other ways too (three months of therapy for a
knee, not six weeks), so they do not measure the new versions. The demo's case 2026-1051, a request for the right
knee when the chart documents the left, and the engine's tests do.

## Retrieval

The query for a criterion is built only from the approved criteria set: its text, policy sentence and attestation
question. A passage counts as found when it overlaps a sentence recorded as bearing on that criterion.

| Held-out (129 queries) | Recall at 1 | Recall at 5 | MRR | Text-only evidence in top 5 |
|---|---|---|---|---|
| Plain BM25 | 0.403 | 0.512 | 0.455 | 3 of 15 |
| Words, after clinical normalization | 0.605 | 0.806 | 0.694 | 10 of 15 |
| Words and trigrams, fused (the default) | 0.496 | 0.899 | 0.669 | 14 of 15 |

On dev the default reached 0.828, 0.984 and 0.899. The drop to held-out is the honest part:

- **Normalization carries it.** Plain BM25 falls from 0.891 recall at 5 on dev to 0.512 on held-out, because
  held-out packets say "DJD", "XR L-spine", "REI" and "Kenalog". The clinical vocabulary, written before any packet,
  closes most of that gap.
- **Trigrams buy recall and cost the top spot.** They recover OCR-damaged and unfamiliar words, which lifts recall
  at 5 from 0.806 to 0.899. They also let near-misses into first place, so recall at 1 is lower than with words
  alone. For a list of five passages a person scans, the trade is worth it. For a single highlighted passage, it
  is not.
- **One phrasing caused most misses.** 8 of the 13 misses are a standing knee film reported as "severe medial
  compartment narrowing", with no word for X-ray. The normalization expects "narrowing of the medial compartment",
  so a chest radiograph distractor ranks higher. 5 more are knee injections and therapy where a distractor
  ("influenza injection", "physical therapy for low back pain") shares the concept and the right one lacks the
  word "knee". The query has no notion of body site.

What the default means in practice: for 14 of the 15 criteria where the record is silent but the packet is not,
the passage a clinician needs to attest is among the first five shown.

## Changes since the held-out run

Two changes to retrieval came after the held-out run, both for the list of passages the product shows under a
criterion the record cannot answer ("where to look"):

1. **Sentence splitting** (`5705586`). A full stop inside a code ("G47.33") no longer ends a sentence. On dev this
   changed nothing measured. On held-out, the default's recall at 5 went from 0.899 to 0.868 and recall at 1 from
   0.496 to 0.504: four queries lost a passage from the top five.
2. **A relevance floor**, in the list only, not in the ranking measured above (`5705586`, then `2f2f136`). Ranking
   always fills a list, so the list shows a passage only when it shares a concept with the criterion, or two words,
   beyond what the service itself names on every page.

What the list shows, measured as the product shows it (the best three that clear the floor, `where_to_look`):

| | Criteria with evidence in the packet | Shown passages include it | Criteria with none | False leads shown per criterion |
|---|---|---|---|---|
| Dev | 64 | 0.953 | 14 | 1.14 (none shown for 43%) |
| Held-out, after the changes | 129 | 0.721 | 27 | 0.96 (none shown for 56%) |

The held-out figure is not a held-out result: the floor was written after that split was run. It is still the
honest number, and it is low. Without the floor, the best three include the evidence for 0.783 of held-out
criteria; the floor removes 12 more. They are of two kinds:

- **OCR damage.** "Screened for red-f1ag symptoms" is ranked by the trigram channel, but the floor counts whole
  words, and "f1ag" is not "flag". A floor that accepts a damaged form of a query word would keep these.
- **Words the vocabulary does not know**: "gonarthrosis" for knee osteoarthritis, and "aching in the lumbar region"
  for low back pain, where "lumbar" is part of the service's own name and so does not count.

Neither is fixed here. A fix for the first can be made and measured on dev, but the held-out split can no longer
say whether it generalizes; that needs a new held-out split. The second is vocabulary, which held-out phrasings
should not be allowed to write.

## How far to trust this

- **The author wrote the engine, the retrieval and the generator.** The engine's record shapes were listed after
  reading the engine, so for the engine this is a stress test of known shapes at a realistic rate, not a blind
  benchmark. The retrieval vocabulary was committed first and held-out phrasings are separate, but the same
  person wrote both.
- **Labels are known by construction.** A label is only as right as the rule that wrote it. 15 held-out labels are
  listed for a person to check in [data/hard/heldout/spotcheck.md](../data/hard/heldout/spotcheck.md), two in three
  from unusual states. They have not been checked yet.
- **The packets are synthetic.** Real faxes have handwriting, tables and stamps, and real notes are longer. The
  next step is the same measurement on de-identified real packets, under a BAA.
- **One held-out split.** The fixes made after it are measured on dev and on a split that is no longer held out.
  A fresh held-out split, from a new seed with new phrasings, would measure them properly.

## What's next

1. Done: a reviewer approved new knee and CPAP versions, with the side and the order, compiled from Medicare's text.
2. Someone checks the 15 spot-check labels.
3. Done: the case shows the passages that may answer an unanswered criterion, and so does the clinician's request
   to sign.
4. A floor that recognizes OCR-damaged words, measured on dev.
5. A second held-out split, then a gate in CI: no unsafe errors on either split, and a floor on what "where to look"
   finds.
