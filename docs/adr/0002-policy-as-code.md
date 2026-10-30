# 2. The model proposes criteria; a deterministic engine decides

Status: Accepted, built (`src/caduceus/criteria/`, `data/policies/`)

## Context

A payer's coverage policy is prose ("conservative therapy for at least six weeks"). Asking a model "is this
patient covered?" gives an answer no one can audit, and it changes when the prompt or the model changes. A
reviewer at the payer, or the clinic's own compliance staff, need to see which sentence of the policy was applied
to which sentence of the chart.

## Decision

* Coverage criteria are data: a small typed DSL (condition, procedure, observation, `any_of`, `all_of`) with the
  policy sentence each criterion came from. A model may draft a criteria set from policy text; a person reviews it.
* Approval records the set's content SHA-256 in `approved.lock.json`. The engine refuses a draft set or a set whose
  content no longer matches its approved hash (`UnapprovedCriteriaSet`).
* The engine is deterministic and returns `met`, `unmet` or `unknown` per criterion, with evidence (the resource,
  and the source sentence in the note it was charted from).
* Missing evidence is `unknown`, never `met`. The one exception is an exclusion ("no active infection") that a
  reviewer has explicitly marked `when_absent: met` with a written rationale.
* `unknown` routes to the ordering clinician for a signed attestation. It is never guessed.

## Consequences

* Every determination is reproducible from the record, the criteria set and its hash. That is checked by
  `test_deterministic`, `test_golden_labels`, `test_empty_record_is_unknown_never_met`,
  `test_removing_evidence_never_leaves_met` and the eval gate (`unknown_reported_as_met` must be 0).
* Policy language the DSL cannot express stays `unknown` and goes to a human, which is the safe failure.
* Criteria sets need a review step, so new policies are slower to onboard than with a pure-LLM approach.

## Alternatives considered

* **LLM-as-judge per case.** Faster to start, but not reproducible and not auditable. Not chosen for decisions; a
  model is still used to read notes and draft letters.
* **A general rules engine (Drools, OPA).** More expressive than needed for three service lines, and the evidence
  trail (which sentence supported which criterion) would still have to be built on top.
