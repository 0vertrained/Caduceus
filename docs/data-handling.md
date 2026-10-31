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
| Faxed referral pages | Yes | Read by OCR inside the container, with no network; the images stay in the intake folder, are shown only to signed-in staff, and are never sent to any model or service. Once a person confirms a fax, the text OCR read from its cover sheet is filed in the case store as page 1 of the case's packet |
| A patient looked up from a fax (name and date of birth) | Yes | Sent in a request body, never a URL; the search runs inside the boundary. Records are named to the browser by a reference made for that fax, not a record number. The audit log keeps the result (found, closest offered, none), never the name |
| A request to the sending clinic for what a fax lacks | No patient identifiers, by construction: the referral number, the clinic and the field names | Shown for a person to send by fax or phone; Caduceus sends nothing. The fax record keeps the words; the audit log keeps the fields and a hash |
| Notes a clinician writes when a payer asks for documentation | Yes | Case store, exactly as signed; sent only to that payer, after a person approves; the audit log keeps a hash |
| The audit log | Treated as PHI: free-text reasons a person types (for example, why a case was returned) can contain it | Same database, append-only; archived to an object-locked S3 bucket |
| Case numbers in URLs and server logs | An internal identifier, kept inside the boundary | API access logs (KMS-encrypted CloudWatch) |
| Text sent to the external model | De-identified only, and only for drafting | Leaves the VPC; nothing else does. Anthropic in the target deployment; Ollama Cloud in the public demo (hosted mainly in the US; Ollama states prompts and replies are not logged or trained on). Each draft keeps a copy of exactly what was sent, which the case page shows |
| Patient names typed into search | Yes | Never leaves the browser tab: the term travels in history state, not the URL, so it cannot reach server or load-balancer logs |
| A patient's record an EHR shared at a SMART launch | Yes | Read by the API over FHIR with the launching person's access token, then filed with the cases, compressed: the name, birth date, sex, and the contact details and identifiers that de-identification needs; every diagnosis; procedures and measurements within the approved criteria's look-back windows; the five newest notes. Narrative text and server metadata are dropped. In the public demo it lives only in the visitor's own copy and goes when that copy starts over or after 6 hours. The browser names it by a random key, never by the EHR's id for the patient. The audit log keeps the FHIR server, counts and scope, never contents |
| A SMART launch's access token, code, PKCE verifier and state | Not PHI, but each can open PHI | Token: in the API's memory for the one request that reads the record; never stored, logged, audited or sent to the browser. Code: carried by the browser once, in the address the EHR sends it back to, which the page clears at once. Verifier and state: in the API's memory only, for at most 10 minutes, and used once |

## Where it flows

1. The agent reads the record through MCP tools. Every call is role-checked, allowed only in the current workflow
   step, and written to the audit log.
2. Tasks that need the identifiable record (summarizing the chart for the clinician) go to the in-VPC model only,
   in the background. If it is down or absent, the summary is written from the record's facts without a model.
   There is no fallback to an outside model. In the public demo this model runs in the same Cloud Run instance as
   the API ([infra/model](../infra/model/Dockerfile)): it listens on 127.0.0.1, has no HTTPS client, serves
   weights baked into its image, and runs with `--offline`. In a production deployment it runs in the VPC's private
   subnet with no outbound route.
3. Drafting gets de-identified facts. The de-identification service replaces identifiers with tokens, stamps the
   result with an HMAC, and the router checks the stamp and re-scans for known identifiers before the text leaves.
   The returned draft is re-identified inside the VPC.
4. A person approves. After a 30-second undo window the request goes to the payer as a FHIR Claim
   (Da Vinci PAS shape), which by necessity contains PHI and travels over the payer's authenticated channel. Until it
   is sent, a PA specialist can still recall it; once sent, nothing is recalled.
5. If the payer pends the request and asks for documentation, the ordering clinician writes the note themselves;
   no model drafts it. After a person approves, it goes by the same channel as an attachment to a Claim related to
   the first. The audit log records a hash of the note, never its words.

A case can also start inside the EHR, by a SMART launch ([docs/smart-launch.md](smart-launch.md)). The API reads
the patient's record with the launching person's authority and files it with the cases. From then on the record
flows exactly as above: drafting still gets only de-identified facts.

## Opening from an EHR: the threat model

In a real deployment a SMART launch brings real PHI into Caduceus: a patient's record, read from the EHR with a
person's authority. In the public demo the EHR is the public SMART sandbox, and every patient in it is synthetic.
The controls below are written for the real case.

| Threat | Control | Tested by |
|---|---|---|
| Someone points Caduceus at their own server, to plant a record or to probe the network | An allowlist of FHIR base URLs, matched exactly and checked before any request: https only, plain http only to the machine itself. Empty outside the demo unless configured. No HTTP redirect is followed | `test_an_iss_off_the_allowlist_is_refused_before_anything_is_fetched`, `test_launch_settings_that_are_not_safe_are_refused` |
| A changed SMART configuration sends the code elsewhere | The authorization and token endpoints must be on the FHIR server's own origin | `test_a_configuration_that_points_elsewhere_or_lacks_pkce_is_refused` |
| A code is finished in the wrong place: an attacker's in a victim's session, or a victim's in an attacker's | The state is single use, good for 10 minutes, and bound to the person and to the demo copy. The redirect URI comes from configuration, never from the request | `test_a_state_is_used_once`, `test_a_launch_started_in_one_copy_cannot_finish_in_another` |
| A code is read from a log, a referrer or the browser history | PKCE S256: a code is useless without the verifier, which never leaves the API. The page clears the code from the address at once; the site sends no referrer | `test_the_ehr_exchanges_the_code_only_for_the_verifier_behind_its_challenge` |
| The access token leaks | It is exchanged and used on the server, in one request. It is never stored, logged, audited or returned | `test_nothing_secret_reaches_the_audit_log_the_record_or_the_browser` |
| A hostile next-page link sends the token to another host | The token goes only to addresses on the FHIR server | `test_a_next_link_off_the_fhir_server_is_never_followed` |
| A huge or slow answer ties up the API | 10 seconds per request, 20 in all; 3 MB streamed and cut off; 1,000 entries; 20 pages. Launch steps are rate-limited per visitor and for everyone. A record over a limit is refused whole, never filed in part | `test_a_record_over_a_limit_is_refused_whole`, `test_a_slow_answer_runs_out_of_time`, `test_launch_steps_are_limited_per_visitor` |
| A server returns another patient's data | Entries whose subject is not the launched patient are dropped and counted | `test_entries_about_anyone_else_are_dropped` |
| Malformed data breaks a case later | The engine and the drafter must be able to read the record before it is filed | `test_a_record_the_engine_cannot_read_is_not_filed` |
| The EHR's error text shows internals | Fixed plain words on the screen; only the OAuth error code is kept | `test_a_refused_exchange_keeps_the_ehrs_own_words_off_the_screen` |
| More is read than the request needs | The scopes name five resource types for one patient. Procedures and measurements are read only as far back as the approved criteria look; narrative text and server metadata are dropped | `test_a_launch_reads_the_patient_within_the_criteria_windows` |
| Caduceus is framed by a hostile page | `frame-ancestors 'none'` stays. The launcher's iframe mode is turned off in the link | The end-to-end walk, which runs the launch with the Content Security Policy enforced |

What is left:

* **The callback address carries the code.** Whatever serves the UI may log it: Netlify in the demo, or uvicorn
  where the API serves the UI. The code is single use, expires in minutes, and is useless without the verifier.
  Where a deployment can, it should keep query strings out of its access logs.
* **The EHR's launch value is the EHR's design.** The SMART launcher encodes its launch options, the patient's id
  among them, in the address it opens. Caduceus passes the value on in a request body, never in an address of its
  own.
* **The EHR user is taken on TLS's word.** The ID token's `fhirUser` is recorded in the audit log after checking its
  audience and expiry, not its signature. It decides nothing.
* **Pending launches live in one process's memory.** A restart drops them, and the person launches again. More than
  one instance needs a shared store.

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
`test_update_and_delete_are_refused_by_the_database`, `test_settings_are_secure_by_default`,
`test_the_payers_question_is_answered_with_the_clinicians_note` (the audit log never holds the note's words),
`test_a_note_changed_after_approval_is_not_sent`,
`test_the_patient_is_found_in_the_chart_or_the_closest_are_offered` (no record number reaches the browser, no name
reaches the audit log), `test_a_fax_missing_something_waits_on_the_clinic_until_they_send_it` (the request to the
clinic never names the patient), `test_nothing_secret_reaches_the_audit_log_the_record_or_the_browser` (no token,
code or verifier in the audit log, the filed record or any response),
`test_the_record_lives_only_in_the_visitors_copy_and_goes_when_it_starts_over`, the end-to-end check that a searched
name never appears in the URL, and the one that a launch's code leaves the address bar.
