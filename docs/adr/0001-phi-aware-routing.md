# 1. Route model calls by whether they carry PHI, and enforce it in types

Status: Accepted, built (`src/caduceus/router/`)

## Context

Prior-authorization work needs language models in two different ways. Reading a chart and summarizing it needs
the identifiable record. Drafting the request letter needs only the clinical facts. The clinic's compliance
officer will accept identifiable data going to a model only if that model runs inside the clinic's own
network boundary (in AWS terms, inside the VPC, covered by the BAA, with no outbound route). Frontier models
write better drafts but sit outside that boundary.

A policy that says "don't send PHI to the external model" is only as good as the most hurried engineer. The
failure we must make impossible is a quiet fallback: the in-VPC model is down, and someone's retry loop sends
the request to the vendor instead.

## Decision

One component, the router, is the only code allowed to call a model. It has two entry points:

* `complete_phi(...)` takes plain strings and only ever reaches the in-VPC provider. If that provider is
  unavailable it raises `InVpcUnavailable`. There is no fallback path in the code to take.
* `complete_deid(...)` accepts only `StampedText`, which only the de-identification service can produce. The
  router verifies each part's HMAC stamp (over the policy version, the text's SHA-256 and the issue time) and
  re-runs the residual identifier check before the external provider sees a byte. With no external provider
  configured, de-identified work runs in-VPC.

Re-identification (putting names back into a draft) happens locally, after the call returns. Telemetry follows
the OpenTelemetry GenAI semantic conventions; spans on the PHI route record model, route, token counts and a
content hash, never content.

## Consequences

* The guarantee is checked by tests, not reviews: `test_no_fallback_when_in_vpc_is_down`,
  `test_external_route_refuses_unstamped`, `..._tampered_stamp`, `..._stamped_text_that_still_holds_an_identifier`,
  the leakage suite (planted identifiers never reach the captured external wire), `test_phi_spans_carry_no_content`
  and `test_import_lint_only_router_imports_providers` (no module outside the router imports a provider or a
  model SDK).
* When the in-VPC model is down, PHI work waits and drafting falls back to a deterministic template. That is
  slower for users but never unsafe.
* De-identification has residual risk (identifiers not known in advance and not matching a pattern). See the
  data-handling note. The external route is used only for drafting, where the patient-specific detail is not needed.

## Alternatives considered

* **Everything in-VPC.** Simplest and safest; draft quality is lower. Kept as the default: the external route is
  off unless an API key is configured.
* **Vendor zero-retention agreements instead of de-identification.** Moves trust to a contract the clinic cannot
  inspect, and a misconfiguration still sends PHI. Not chosen.
