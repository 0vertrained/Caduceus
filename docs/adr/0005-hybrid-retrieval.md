# 5. Hybrid retrieval over the record: full-text and vectors, fused

Status: Proposed (not built in the first slice)

## Context

Today the criteria engine reads structured FHIR resources, and the referral packet is small enough to show
whole. Real packets run to dozens of faxed pages, and the evidence for a criterion is often one sentence in one
note. Pure vector search misses exact terms (drug names, CPT codes, laterality); pure keyword search misses
paraphrase ("failed PT" for "conservative therapy").

## Decision (proposed)

Index each packet page and note in Postgres twice: a full-text index and a pgvector embedding computed by the
in-VPC model (the text is PHI). Query both and fuse the ranked lists with Reciprocal Rank Fusion. Retrieval
results are candidates only: a criterion is still `met` only when the engine finds the structured evidence or a
person confirms the sentence.

## Consequences

* One database for state, audit and retrieval; no separate vector store holding PHI.
* Embeddings must be computed in-VPC, which ties retrieval quality to the in-VPC model.
* Needs an evaluation set of (criterion, page, sentence) labels before it ships; the eval harness gets a
  retrieval suite (recall at k).
