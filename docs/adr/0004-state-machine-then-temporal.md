# 4. A database state machine with an outbox first; Temporal later

Status: Accepted, built for SQLite (`src/caduceus/workflow.py`, `cases.py`); Postgres and Temporal on the roadmap

## Context

A case waits for days: for a clinician's signature, for the payer. Work must survive restarts, a request must
never be sent twice, and a specialist who approves by mistake needs a way back before anything leaves the building.

## Decision

* Cases move through explicit states (`review`, `awaiting_attestation`, `submitted`, `pended`, `approved`,
  `denied`, `returned`). Each save checks an optimistic version number, so two people cannot overwrite each
  other (`StaleCase`).
* Approval does not send. It writes an outbox entry with a send-after time 30 seconds out. Until then the same
  person can undo, which restores the prior state from a snapshot. A dispatcher sends due entries; it holds a
  lock so concurrent dispatchers send each request exactly once (`test_concurrent_dispatchers_send_each_request_once`).
* When a clinician signs, the agent rewrites the draft so the letter matches the signed answers. A draft a person
  has edited is never overwritten: it is marked out of date and approval waits until someone re-reads and saves it.
* State lives in SQLite today. The audit log's append-only guards are already written for Postgres
  (`infra/sql/audit.sql`: no UPDATE or DELETE grant, plus a trigger); the case repository is next, and RDS in
  the Terraform skeleton is for that move.

## Consequences

* Simple to run and reason about; everything is a row.
* Timers and retries are hand-written. When there are more steps that wait on the outside world (appeals, payer
  callbacks), Temporal's durable execution earns its operational cost; this ADR is revisited then.

## Alternatives considered

* **Temporal from day one.** Strong guarantees, but a cluster to operate before there is a workflow complex
  enough to need it.
* **A queue (SQS) with workers.** Gives retries but not the case-level state the UI needs to show.
