# Data handling note

Written for the clinic's sponsor and compliance officer. It says what protected health information (PHI) this
system holds, where it goes, what the de-identification step removes, and what risk is left. Everything in this
repository is synthetic (Synthea patients and generated notes); this note describes how the system would treat
real data.

## What is PHI here

| Data | PHI? | Where it lives |
|---|---|---|
| The patient record (FHIR resources), the referral packet, visit notes | Yes | The EHR (read through the `ehr` and `documents` tools); copies of relevant sentences in the case store |
| Determinations, evidence sentences, draft request letters | Yes | Case store (SQLite today, Postgres on RDS in the target deployment), encrypted at rest with KMS |
| The audit log | Treated as PHI: free-text reasons a person types (for example, why a case was returned) can contain it | Same database, append-only; archived to an object-locked S3 bucket |
| Case numbers in URLs and server logs | An internal identifier, kept inside the boundary | API access logs (KMS-encrypted CloudWatch) |
| Text sent to the external model | De-identified only, and only for drafting | Leaves the VPC; nothing else does |
| Patient names typed into search | Yes | Never leaves the browser tab: the term travels in history state, not the URL, so it cannot reach server or load-balancer logs |

## Where it flows

1. The agent reads the record through MCP tools. Every call is role-checked, allowed only in the current workflow
   step, and written to the audit log.
2. Tasks that need the identifiable record (reading notes, summarizing the chart) go to the in-VPC model only. If
   it is down, the work waits. There is no fallback to an outside model.
3. Drafting gets de-identified facts. The de-identification service replaces identifiers with tokens, stamps the
   result with an HMAC, and the router checks the stamp and re-scans for known identifiers before the text leaves.
   The returned draft is re-identified inside the VPC.
4. A person approves. After a 30-second undo window the request goes to the payer as a FHIR Claim
   (Da Vinci PAS shape), which by necessity contains PHI and travels over the payer's authenticated channel.

## What de-identification removes

Safe Harbor lists 18 identifier types. The service removes:

* **Values known for this patient** from the record: every name and name part, the birth date in four formats,
  street address lines, city, ZIP, phone numbers, email addresses and record numbers.
* **Patterns, whoever they belong to:** email addresses, URLs, IP addresses, Social Security numbers, phone
  numbers, labelled record numbers (MRN, member ID and similar), dates in three formats, street addresses,
  state-plus-ZIP, and ages over 89 (rewritten as "90 or older").

Clinician names are not removed; they are not identifiers of the patient under Safe Harbor.

## Residual risk

* **Identifiers that are neither known nor patterned**, such as a relative's name written in a note ("her
  daughter Maria drove her"). The residual check cannot catch a value it has never seen. Mitigations: the external
  route is used only for drafting, which needs clinical facts rather than narrative; drafts use structured facts
  plus quoted evidence sentences. Planned: a clinical NER pass before stamping.
* **Rare combinations** of age, diagnosis and dates can narrow a person down even without direct identifiers. A
  deployment that wants a formal guarantee should use Expert Determination rather than Safe Harbor.
* **The external vendor** receives de-identified text under its own retention terms. Leave
  `CADUCEUS_EXTERNAL_API_KEY` empty and every call stays in-VPC.

## Telemetry, logs and browsers

* Model-call spans follow the OpenTelemetry GenAI conventions. PHI-route spans carry the model, route, token counts
  and a content hash, never content. Capturing de-identified content on external-route spans is off unless
  `CADUCEUS_CAPTURE_DEID_CONTENT=1`.
* API paths contain case numbers, never names. Request bodies are not logged. Database statement logging is off
  on purpose (it would copy query parameters into logs). WAF logs redact the Authorization header and keep no
  sampled requests.
* Every API response is `Cache-Control: no-store`. The browser stores only the text-size choice (and, in
  development, which demo user is signed in). No PHI is kept in browser storage.

## Controls that are tested, not just stated

`test_no_fallback_when_in_vpc_is_down`, `test_external_route_refuses_unstamped`,
`test_external_route_refuses_tampered_stamp`, `test_external_route_refuses_stamped_text_that_still_holds_an_identifier`,
the leakage suite (planted identifiers never reach the captured external wire; part of the eval gate),
`test_phi_spans_carry_no_content`, `test_import_lint_only_router_imports_providers`,
`test_update_and_delete_are_refused_by_the_database`, `test_settings_are_secure_by_default`, and the end-to-end
check that a searched name never appears in the URL.
