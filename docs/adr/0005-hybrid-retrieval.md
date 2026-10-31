# 5. Hybrid retrieval over the record: full-text and vectors, fused

Status: Accepted, partly built: two lexical channels fused; the dense channel waits for an in-VPC embedding model

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

## What was built (October 31, 2026)

`caduceus.retrieval` ranks a packet's sentences in process, with nothing leaving the boundary. Two channels are
fused by Reciprocal Rank Fusion: word BM25 after clinical normalization (abbreviations and synonyms become one
concept, so "PT x 8 wks" matches "physical therapy"), and character-trigram BM25, which survives OCR damage. Both
are lexical, so the paraphrase case the decision names is handled by the vocabulary, not by vectors. A dense
channel plugs in through one function once an embedding model runs in the VPC. It is in memory over one packet,
not yet in Postgres.

Measured on the [hard sets](../hard-evaluation.md): on held-out phrasing, the evidence is in the top 5 passages for
0.899 of criteria (plain BM25: 0.512), and first for 0.496. The query comes only from the approved criteria set, so
packet text cannot steer it, and results stay candidates: the engine still decides from structured evidence or a
person's confirmation.
