# Opening Caduceus from an EHR (SMART on FHIR)

A prior-authorization specialist works inside the EHR. With a SMART EHR launch, they open Caduceus from the
patient's chart: Caduceus reads that patient's record over FHIR R4 and they start a prior-authorization case from it,
checked by the same engine, sent to the clinician for the same attestation, drafted and audited the same way as a
faxed or seeded referral.

In the public demo, the [SMART Health IT launcher](https://launch.smarthealthit.org) plays the EHR. Its patients are
synthetic. The Help page links to it, already set up for Caduceus.

## How it works

Caduceus is a SMART App Launch client (EHR launch, version 2 scopes when the server offers them). The browser only
navigates: to the EHR's authorization page and back. The API does everything else, on the server:

- **Discovery, state and PKCE.** It reads the EHR's `.well-known/smart-configuration`, makes a `state` and a PKCE
  verifier, and keeps both in its own memory.
- **The code exchange.** It exchanges the authorization code for an access token itself. The token never reaches
  the browser and is never written anywhere. It is used for one read, in the request that received it.
- **One bounded read.** With that token it reads the launched patient's record, and only the resource types the
  running criteria can use, as far back as they look. The windows come from the criteria sets that run
  (`caduceus.smart.lookback`):
  - every diagnosis, since a criterion may ask how long one has lasted;
  - procedures, and measurements and test results, as far back as the longest look-back among the running sets.
    Today that is the whole history of both: the CPAP criteria, compiled from Medicare's text, accept a sleep test
    from any date and ask that the evaluation came before it. (With the earlier development criteria it was two
    years of procedures and one of measurements.) Reading for the one service the person chooses, rather than for
    every service, is the next change;
  - the newest notes, which become the case's pages.
- **Filing.** The record is filed with the cases. In the demo that is the visitor's own copy, so it goes when the copy
  starts over or expires. The person sees what the record holds, and how much of each service's criteria it answers,
  before they start the case.

The scopes ask for nothing more: `launch openid fhirUser patient/Patient.r patient/Condition.rs
patient/Procedure.rs patient/Observation.rs patient/DocumentReference.rs`. A SMART v1 server is asked for the same
in v1 syntax (`patient/Condition.read` and so on).

## The sequence

1. In the EHR, a person opens Caduceus for a patient. The EHR loads `/smart/launch?iss=<its FHIR base>&launch=<an
   opaque launch>`. In the public demo, whoever launches acts as the demo's PA specialist, Dolores Montoya.
2. The page sends `iss`, `launch` and its redirect URI to `POST /api/smart/launch`, in the request body, so they stay
   out of access logs.
3. The API refuses an `iss` that is not on its allowlist, and a redirect URI it was not configured with. Both checks
   happen before any request goes out.
4. The API reads `<iss>/.well-known/smart-configuration`, within 10 seconds and 64 KB. It refuses the configuration if
   the authorization or token endpoint is off the FHIR server's own origin, or if the server does not offer PKCE S256.
5. The API makes a `state` (32 random bytes) and a PKCE verifier (64 random bytes). It keeps them in memory for at
   most 10 minutes, bound to the person and to the visitor's copy of the demo, and returns the authorize URL:
   `response_type=code`, `client_id`, `redirect_uri`, `launch`, `scope`, `state`, `aud=<iss>`, and the S256
   `code_challenge`.
6. The browser goes to the EHR's authorization page. The SMART launcher may ask for a practitioner and a patient
   here. The EHR then sends the browser to `/smart/callback?code=…&state=…`.
7. The page takes the code out of the address bar and the browser history at once, and sends the code and state to
   `POST /api/smart/callback`.
8. The API takes the state out of memory, so it is spent whatever happens next. It checks that the same person, in
   the same copy, started the launch less than 10 minutes ago. Then it exchanges the code with the verifier at the
   token endpoint.
9. With the access token, the API reads, within 20 seconds, 1,000 entries and 3 MB in all:
   - `Patient/<id>`;
   - `Condition?patient=<id>`;
   - `Procedure?patient=<id>`, with `&date=ge<the start of the window>` when the running criteria set one;
   - `Observation?patient=<id>`, the same way;
   - `DocumentReference?patient=<id>&_sort=-date&_count=10`.

   It follows next-page links only while they stay on the FHIR server, and drops any entry about anyone else.
10. The API checks that the engine and the drafter can read the record, files it, and records `ehr.record_read` in the
    audit log: the FHIR server, the counts, the scope granted, and the EHR user named in the ID token. The audit log
    never holds a token or the record's contents. The token goes out of scope with the request. The browser gets an
    opaque key for the record.
11. The page opens `/smart/record/<key>`. It shows the patient (name, age, sex), what the record holds in plain
    words, and, for each of the three approved criteria sets, how many criteria the record answers today.
12. The person chooses what is being requested, the knee for a knee replacement, and the health plan. The launch read
    the clinical record, not the coverage. `POST /api/smart/records/<key>/case` creates the case through the
    workflow. The audit log's `case.received` event names the FHIR server and `source: smart_launch`. From here the
    case is like any other.

## Try it

**On the public demo.** Open Help, then *Open from an EHR*, and choose *Open the SMART launcher*. In the launcher,
choose a patient, for example one with low back pain, knee osteoarthritis or sleep apnea, and choose *Launch*.
Choose a practitioner if it asks. Caduceus opens on the patient's record.

Expect most criteria to come back *Not found*. Synthea's records use SNOMED and LOINC codes, and none of this
project's local observation codes (a red-flag screen, a joint-space finding, an apnea-hypopnea index). Caduceus
answers what the record shows and asks the ordering clinician for the rest. That is the honest result, and the
attestation path is the point. The page says how many criteria the record answered before the case opens.

Replies from the draft model are recorded at deploy time for the seeded cases only (`scripts/record_completions.py`).
A launched case drafts live, within the demo's 20-second model timeout, or falls back to the template. The case page
says which.

**On your machine, against a fake EHR** (`tests/fake_smart.py`, which the tests and the end-to-end walk use):

```
python -m tests.fake_smart --port 8766 &
CADUCEUS_SMART_ISS=http://127.0.0.1:8766/v/r4/fhir \
CADUCEUS_SMART_REDIRECT_URIS=http://localhost:5173/smart/callback make dev
```

Then open `http://localhost:5173/smart/launch?iss=http://127.0.0.1:8766/v/r4/fhir&launch=x` and sign in as Dolores
Montoya. To use the real launcher instead, set `CADUCEUS_SMART_ISS=https://launch.smarthealthit.org/v/r4/fhir`. The
Help page's launcher link then opens Caduceus at your own address.

| Setting | What it does | Default |
|---|---|---|
| `CADUCEUS_SMART_ISS` | FHIR base URLs Caduceus may be launched from, separated by spaces or commas. Matched exactly. https only; plain http only to this machine | The SMART sandbox in demo mode; none otherwise |
| `CADUCEUS_SMART_REDIRECT_URIS` | This site's `/smart/callback` address(es). The launch is off until one is set | None |
| `CADUCEUS_SMART_CLIENT_ID` | The client id the EHR registered Caduceus under | `caduceus-demo` |

The launcher link (`ui/src/smart.ts`) presets a provider EHR launch for the R4 server: a public client, PKCE always,
Caduceus's client id and redirect URI (the launcher then checks both, as a registered EHR would), skipped login and
approval screens where the launcher allows, and *Simulate EHR* off. It presets no patient or practitioner: see
"What is not done".

## Security decisions

- **The token never reaches the browser, and is never stored.** The code exchange and the read happen on the server,
  in one request. The token is not written to the database, the audit log, a log line or a response.
- **A public client with PKCE.** The demo registers no secret, so PKCE (S256) protects the code. The launcher is told
  to require PKCE. A real deployment adds client authentication (below).
- **State.** The state is 256 random bits, used once (it is removed before any check), good for 10 minutes, and bound
  to the person and to the visitor's copy. A code started by one visitor cannot be finished in another visitor's copy.
- **The redirect URI comes from configuration, never from the request.** An attacker cannot get a code sent to their
  own address through Caduceus.
- **Only allow-listed FHIR servers.** The match is exact, against https base URLs, and is checked before any request.
  The allowlist is empty outside the demo unless configured. The settings refuse unsafe values at startup.
- **The code and token go only to the FHIR server's own origin.** A changed SMART configuration cannot point the
  token endpoint elsewhere. Next-page links are followed only while they stay on the FHIR server, so the token is
  sent nowhere else. No HTTP redirect is followed.
- **Bounded reads.** Each request may take 10 seconds; the whole callback, 20, which keeps it inside Netlify's
  26-second proxy limit. Answers are streamed and cut off at 3 MB in all. There are at most 1,000 entries and 20
  pages per search. A record over a limit is refused whole, never filed in part: a diagnosis list read in part could
  hide the joint infection that the knee criteria exclude.
- **The least data.** The scopes and the date windows come from the approved criteria. Narrative text and server
  metadata are dropped. The patient keeps only their name, birth date, sex, and the contact details and identifiers
  that de-identification removes before any model sees the facts.
- **What comes back is checked.** Each entry needs the expected resource type, a valid FHIR id and the launched
  patient as its subject; other entries are dropped and counted. The patient needs a name and a full birth date. The
  engine and the drafter must be able to read the record before it is filed.
- **Bounded inputs.** Every body has `Field(max_length=…)`. The state must match its own format. The launch value and
  the code must be printable ASCII of at most 2,048 characters.
- **Plain errors.** Each failure has fixed words that say what to do next. The EHR's own error text is never shown
  or stored; the audit log keeps only the OAuth error code, such as `invalid_grant`.
- **Rate limits.** Each visitor may take 10 launch steps per five minutes, and all visitors together 200. These come
  on top of the demo's other limits. Each step can make requests to the EHR.
- **The demo's copies.** A launch must name the visitor's own copy, or it is refused. Records go when the copy starts
  over or after 6 hours. One copy holds at most 3 patients from an EHR.
- **The EHR user.** The ID token's `fhirUser` (for example `Practitioner/…`) is recorded in the audit log and used for
  nothing else. It is taken on the word of TLS, as OpenID Connect allows for a token from the token endpoint (Core
  3.1.3.7); its audience and expiry are checked.
- **The Content Security Policy is unchanged.** The browser fetches only `/api` (`default-src 'self'` covers
  `connect-src`). The launcher and the EHR's authorization page are navigations, which the policy does not restrict.
  `frame-ancestors 'none'` stays, so the launcher's *Simulate EHR* mode, which frames the app, is refused; the link
  turns that mode off. The end-to-end walk runs the whole launch with the policy enforced and fails on any
  violation.

## What a real EHR deployment adds

- **Registration with the health system's EHR.** The client id, the exact launch and redirect URIs, and the scopes
  the EHR analyst approves. The EHR's authorization server then enforces them; the demo has the launcher check the
  client id and redirect URI the same way.
- **A confidential client.** The token request is authenticated with `private_key_jwt` (SMART's asymmetric client
  authentication), its key in KMS, in addition to PKCE.
- **Sign-in from the EHR.** The ID token's signature is checked against the EHR's published keys, and its `fhirUser`
  is mapped to a Caduceus user and role (the API side of OIDC sign-in is built). In the demo, the launch acts as
  Dolores Montoya.
- **Backend Services for the agent.** The agent's own reads (re-checking a case later, a resubmission) go through SMART
  Backend Services: `client_credentials` with a signed JWT assertion, and system scopes limited to these resource
  types. No person has to launch again ([rollout plan](rollout-plan.md)).
- **The order and the coverage from the launch.** Instead of asking the person:
  - the order being authorized and its requester (`fhirContext`, `ServiceRequest`) give the service, the side and
    the ordering clinician;
  - `patient/Coverage.rs`, or the payer's CRD and DTR services, gives the plan and member ID.
- **Embedding, if wanted.** If the EHR shows Caduceus in a frame, `frame-ancestors` names exactly the EHR's origin.
- **Production storage.** The record is filed in Postgres with the case, encrypted, and kept as long as the case. The
  pending launches move to a shared store with a TTL, so more than one instance can serve the callback; the demo
  runs one.
- **Larger records.** Higher caps for a hospital chart, or a bulk read under Backend Services.
- **Laterality.** The knee criteria learn which knee a diagnosis is for, so that the side chosen here is checked, not
  only named ([hard sets](hard-evaluation.md)).

## How it is tested

- `tests/test_smart.py` covers the protocol, against the fake SMART server through `httpx.MockTransport`:
  - discovery, the authorize URL, PKCE S256 and the scopes in both syntaxes;
  - a refused `iss`, redirect URI or launch;
  - configurations that point elsewhere or lack PKCE;
  - a state that is reused, expired, or finished by someone else or in another copy;
  - a verifier that does not match its challenge, and the EHR's error text kept off the screen;
  - paging, next links off the server, the limits on entries, bytes and pages, and timeouts;
  - entries about other patients, an incomplete patient, grants narrower than asked for, and an ID token for
    another app.
- `tests/test_ehr_launch.py` covers the same flow through the HTTP API:
  - the record view, and the record kept only in the visitor's copy, gone after starting over or expiring;
  - a case through attestation, approval and the payer;
  - the knee side through to the request's LT or RT modifier, and duplicates refused;
  - at most three patients per copy, and a record the engine cannot read is not filed;
  - nothing secret in the audit log, the filed record or any response;
  - the rate limits, bounded bodies, the feature off outside the demo, and a live draft falling back to the
    template.
- `ui/e2e/run.mjs` walks it in Chromium against the fake server over HTTP, with the launcher's page answered by
  Playwright, so CI never calls the real launcher. It runs with the Content Security Policy enforced. It checks the
  launcher link's preset, the code leaving the address bar, the error summary, Enter to submit, and refusals in plain
  words, with axe-core (WCAG 2.2 AA) on each new screen and the record page at 320 px.

## What is not done

- **A launch against the real launcher has not been run.** The build environment's network refused
  `launch.smarthealthit.org` and `r4.smarthealthit.org`. The launcher's option order and codes come from its public
  documentation and from a third party's working use of it, and the protocol is covered against the fake server.
  The first real launch is the check.
- **No preset patient or practitioner.** No sandbox patient with low back pain, knee osteoarthritis or sleep apnea
  could be confirmed from here, and a wrong id would break the launch. The launcher asks the visitor to choose. To
  preset one, set `SANDBOX_PATIENT` (and `SANDBOX_PRACTITIONER`, which also skips the launcher's practitioner login)
  in `ui/src/smart.ts`.
- **SMART v1 servers without `.well-known/smart-configuration`.** Discovery through the CapabilityStatement's OAuth
  extensions is not read.
