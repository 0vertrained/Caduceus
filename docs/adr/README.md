# Architecture decision records

| # | Decision | Status |
|---|---|---|
| [1](0001-phi-aware-routing.md) | Route model calls by whether they carry PHI, enforced in types | Accepted, built |
| [2](0002-policy-as-code.md) | The model proposes criteria; a deterministic engine decides | Accepted, built |
| [3](0003-mcp-integration-boundary.md) | MCP is the integration boundary; access enforced server-side | Accepted, built |
| [4](0004-state-machine-then-temporal.md) | A database state machine with an outbox first; Temporal later | Accepted, built (SQLite) |
| [5](0005-hybrid-retrieval.md) | Hybrid retrieval: full-text and vectors, fused | Proposed |
| [6](0006-confidence-routing.md) | Route by confidence: low-confidence extraction goes to a person | Proposed |
