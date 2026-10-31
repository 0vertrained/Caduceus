# De-identification removed ordinary words, and the model repeated the placeholder

**Date:** October 30, 2026
**Severity:** low. No patient information was exposed. The effect was awkward wording in a letter meant for a payer.
**Found by:** reading the first replies recorded from Kimi K2.6, before they were committed.

## Summary

The appeal letter Kimi K2.6 drafted for the denied CPAP request said: *"The cited coverage policy,
[RECORD_NUMBER_1], states: …"*. The model had been given the label *"Coverage policy [RECORD_NUMBER_1]:"*
instead of *"Coverage policy sentence:"*. One of the de-identifier's patterns had read the words "policy sentence"
as an insurance policy number and replaced "sentence" with a placeholder. After the reply came back, the
placeholder would have been filled in with "policy sentence", so the payer would have read "The cited coverage
policy, policy sentence, states".

## Cause

The pattern for labelled record numbers looks for a label (MRN, member ID, account, *policy*, subscriber, license,
serial), optional punctuation, and then any run of four or more letters or digits, ignoring case:

```
policy(?: number)?\s*(?:#|no\.?|number|ID)?\s*[:#]?\s*[A-Z0-9][A-Z0-9-]{3,}
```

"policy sentence" fits that: the label "policy", then "sentence". The appeal's facts were written with the label
"Coverage policy sentence:" after the pattern existed, and nothing checked the new label against it.

## Why the safeguards did not catch it

The eval gate's leakage suite plants 181 identifiers and checks that none reaches the model. It measures
**under-removal**, the dangerous direction, and it passed. Nothing measured **over-removal**: text that carries no
patient information being taken out. Over-removal is not a privacy failure, but it changes what the model is
told, and here it reached a letter.

## What changed

- **A record number must contain a digit** (`4d28ec5`). Medical record, member, policy and account numbers all do.
  The patient's own record numbers are still removed by the known-values pass, whatever their form. The leakage
  suite still finds 0 of 181 identifiers reaching the model.
- **A test for over-removal.** `test_the_fixed_text_prompts_are_built_from_loses_nothing` runs every fixed piece
  of text the prompts are built from through the de-identifier: the facts' labels, every criterion, every policy
  sentence and every attestation question. It requires that nothing is removed. It fails on the old pattern.
- **The appeal was recorded again.** The next deploy recorded 22 replies, including the corrected appeal.
- **The bake-off flags invented placeholders.** A reply with a placeholder that is not in its facts is counted
  against the model, which would also catch a model inventing one.

## Lessons

- A de-identifier has two failure directions, and the gate tested only one. Both now have a test.
- Text written for the model (labels, headings, instructions) is input to the de-identifier too. It needs the same
  review as any other input.
- Reading real model output before trusting it found what the automated checks missed. Recorded replies are kept in
  the repository so that anyone can do the same.
