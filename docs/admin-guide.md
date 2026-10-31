# Admin guide

For the person who sets up and looks after a Caduceus deployment: who can do what, the administration page, the
coverage policies, the audit log, and every setting. For what to do when something goes wrong, see the
[runbook](runbook.md).

## Roles

| Role | Sees | Can do |
|---|---|---|
| Prior authorization specialist | The queue, cases, the fax inbox, outcomes, policies | Check faxes and create cases; ask a clinician to attest; approve, recall, return and resubmit; mark a request expedited; send notes and appeals |
| Clinician | Only the requests that wait on them | Answer and sign attestation questions; write the note a payer asked for, or say they cannot |
| Administrator | The queue and cases, read only; outcomes; policies; the administration page | Switch the external model off or on; check that the audit log is intact |
| Auditor | The queue and cases, read only; outcomes; policies | Check that the audit log is intact |

A role comes from sign-in: in development and the demo, from the person chosen on the sign-in page; with OIDC,
from the `caduceus_role` claim your identity provider puts in the token, and a clinician's `clinician_id`. A token
without a valid role cannot sign in. The service identity the agent runs as cannot sign in to the screens at all.

## The administration page

Signed in as an administrator, choose **Administration** in the header.

- **Where patient information can go.** The model inside Mesa Verde's network, which does work that needs the
  patient's details, and the model outside it, which only ever receives de-identified text that is checked again
  for the patient's identifiers before each request leaves. Model names are shown; keys and addresses never are.
- **The external model's switch.** "Turn off the external model" stops every call to it at once. De-identified
  work then runs on the in-network model, or, if there is none, drafts come from the template, and each case says
  so. Each switch is recorded in the audit log with who did it, and the last one holds after a restart. In the
  demo, it applies to your own copy only.
- **Coverage policies.** Each service's running version, and whether it is unchanged since it was approved.
- **The audit log.** How many events, the last one, and whether each is still sealed to the one before.
- **People and roles.** In development and the demo, the people who can sign in. With OIDC, people come from your
  identity provider.
- **Systems.** What holds the cases and audit log (SQLite or Postgres), where patient records come from (bundles
  on disk, a HAPI FHIR server, or the EHR), how people sign in, and how quickly the payer decides.

## Coverage policies

Choose **Policies** in the header. For each service: the version that runs now, the document its words were compiled
from (linked) and when, who approved it and when, and its fingerprint. Then each criterion, with the policy sentence
it quotes word for word and the codes it looks for, and the versions it replaced.

A version runs only when all of these hold:

- its file says `status: approved`, with who approved it and when;
- its content hash is in `data/policies/approved.lock.json`, written by `make approve-policy KEY=<id>@<version>`;
- the file has not changed since: one changed character changes the hash, and the version stops running;
- no other approved version names it in `supersedes`.

To add or change criteria, write a new version rather than editing an approved one, then follow
[Approve a new criteria set](runbook.md#approve-a-new-criteria-set). Each criteria set's YAML file says, in comments,
which rules are this team's operating rules rather than the policy's words.

## The audit log

Every action is an event: who (a person, or the agent's service identity), their role, what, which case, and when,
each sealed with a hash of the one before. The case history shows a case's events in plain words; the outcomes page
counts them.

What it never holds: a patient's name or other identifiers, the clinical reason given for expediting, the text of
a draft or a note (only a hash of it), or anything sent to a model (only its hash and the counts of identifiers
removed). On Postgres the table refuses updates and deletes even from the application's own role
([postgres.md](postgres.md)).

## Settings

Set as environment variables. The API checks them at start and refuses to start on an unsafe combination.

| Setting | Default | What it does |
|---|---|---|
| `CADUCEUS_AUTH_MODE` | `oidc` | `oidc` for real sign-in; `dev` (local) and `demo` (the public demo) let a person choose who they are |
| `CADUCEUS_OIDC_ISSUER`, `CADUCEUS_OIDC_AUDIENCE`, `CADUCEUS_OIDC_JWKS_URL` | none | Your identity provider: who issues tokens, the audience they must carry, where its signing keys are. Required with `oidc` |
| `CADUCEUS_DEID_KEY` | none | The key that seals de-identified text and its placeholders. At least 32 bytes; required with `oidc`. Development makes a new one each start |
| `CADUCEUS_DATABASE_URL` | none | A `postgresql://` URL: cases and the audit log in Postgres. Refused in the demo |
| `CADUCEUS_DB` | `var/caduceus.sqlite3` | The SQLite file, when no database URL is set |
| `CADUCEUS_DATA_DIR` | `data` | Policies, scenario data, the fax folder and recorded replies |
| `CADUCEUS_FHIR_BASE_URL` | none | A FHIR server for patient records; without it, the bundles in the data folder |
| `CADUCEUS_IN_VPC_URL`, `CADUCEUS_IN_VPC_MODEL` | none, `llama3.1:8b` | The model inside the network, an OpenAI-compatible server. The demo and docker compose run [infra/model](../infra/model/Dockerfile) as `qwen2.5-1.5b-instruct` |
| `CADUCEUS_EXTERNAL_PROVIDER` | `anthropic` | The external model's vendor: `anthropic`, `ollama`, or `stub` (development only) |
| `CADUCEUS_EXTERNAL_API_KEY`, `CADUCEUS_EXTERNAL_MODEL`, `CADUCEUS_EXTERNAL_BASE_URL` | none | The external model. With `oidc`, a key without an in-network model is refused: work that needs the patient's details would have nowhere to run |
| `CADUCEUS_EXTERNAL_TIMEOUT` | `60` | Seconds to wait for the external model before the template writes the draft |
| `CADUCEUS_MODEL_RECORDINGS` | none (the demo uses `data/recorded/completions.json`) | Recorded external replies to replay |
| `CADUCEUS_CLINIC_TZ` | `America/Denver` | The clinic's time zone, for every time shown |
| `CADUCEUS_PAYER_REVIEW_SECONDS` | `0` | How long the simulated payer takes to decide; half when expedited |
| `CADUCEUS_SHOWCASE` | `0` | `1` adds the demo's three showcase cases |
| `CADUCEUS_DEMO_RESET_HOURS` | `6` | How often each demo visitor's copy starts over |
| `CADUCEUS_PROXY_SIGNING_KEY` | none | With it, only requests Netlify proxied and signed are answered. At least 32 bytes |
| `CADUCEUS_CAPTURE_DEID_CONTENT` | off | `1` puts de-identified prompt text into traces, for debugging only |
| `CADUCEUS_UI_DIST` | `ui/dist` | Where the built UI is served from |

The demo refuses a model key billed per token: anyone can use the demo, so only a fixed-quota key may run there.
