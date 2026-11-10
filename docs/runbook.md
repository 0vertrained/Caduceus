# Runbook

What to do, step by step, to run Caduceus and to answer when something goes wrong. Each procedure names the place
it runs: the public demo (Netlify in front of Cloud Run), the local stack (`make up`, docker compose), or a
production deployment (the AWS design in [infra/terraform](../infra/terraform/README.md)). Settings are listed in
the [admin guide](admin-guide.md#settings).

## Is it up?

| Check | How | Healthy |
|---|---|---|
| The API answers | `curl -fsS https://<host>/api/health` (for the demo, the Cloud Run address) | `{"ok": true}` |
| The demo's API answers through its site | `curl -fsS https://caduceusdemo.netlify.app/api/auth-config` | `{"mode":"demo"}` |
| The demo's site answers stray API requests itself | `curl -s -o /dev/null -w '%{http_code}' https://caduceusdemo.netlify.app/api/health` | `404` from Netlify, and the server is not started |
| The demo's API refuses anyone but Netlify | `curl -s -o /dev/null -w '%{http_code}' <cloud-run-url>/api/demo` | `403` (the deploy checks this every time) |
| The audit log is intact | Administration page, "The audit log"; or as an auditor, Case history, "Check that nothing was changed" | "Intact", with the event count |
| Every policy version runs | Administration page, "Coverage policies" | Each "unchanged since it was approved" |
| Requests leave the outbox | A case approved more than 30 seconds ago is no longer "Sending" | No `dispatch.error` events in the audit log |
| The models | Administration page, "Where patient information can go" | The routes you expect, and the external model on or off as decided |

## Routine

### Deploy

- **Demo.** Push to `main`. CI runs (lint, tests, the eval gate, the hard sets, the end-to-end walk with
  accessibility checks, the Postgres tests, the image scan); when it passes, `deploy-demo` records the draft model's
  replies, builds the image, deploys it to Cloud Run and checks that it refuses requests Netlify did not sign.
  Netlify builds the UI from the same commit. Nothing is deployed from a branch or by hand.
- **Local stack.** `make up` builds the image and starts the API and Postgres; with the `llm` profile, also the
  in-network model, on a network with no route out (see [postgres.md](postgres.md) for the database's first
  start).

### Roll back the demo

Cloud Run keeps earlier revisions. List them and send all traffic to the last good one:

```
gcloud run revisions list --service caduceus-demo --region us-central1
gcloud run services update-traffic caduceus-demo --region us-central1 --to-revisions <revision>=100
```

Then revert the commit on `main`, so the next deploy does not bring the problem back.

### Start the demo's cases over

Each visitor's copy starts over after 6 hours (`CADUCEUS_DEMO_RESET_HOURS`) or when they choose "Start over now".
A new revision starts every visitor fresh. Nothing to do by hand.

### Approve a new criteria set

1. The UM nurse reads the new version beside its source, criterion by criterion: each `policy_sentence` must be the
   payer's own words, and each code must mean what the criterion asks for.
2. They approve it: `make approve-policy KEY=<id>@<version>`, which writes its content hash to
   `data/policies/approved.lock.json`. A version that names the one it replaces (`supersedes`) takes over that
   service once approved.
3. Commit the version and the lock together, with the reviewer named in the commit message; CI runs the eval gate on
   it.
4. After deploying, the policies page shows the new version running, and new cases are checked against it. Cases
   already decided keep the version they were decided under.

### Rotate a secret

| Secret | Where it lives | Rotate |
|---|---|---|
| De-identification key (`CADUCEUS_DEID_KEY`) | Secret Manager or the task's secret | Set a new value and redeploy. Placeholders in drafts already written stay as they are; new ones use the new key |
| Proxy signing key (demo) | Secret Manager `caduceus-proxy-signing-key` and Netlify's environment | Set the same new value in both, then redeploy both; requests fail with 403 until both match |
| External model key | Secret Manager (`infra/demo/add-model-key.sh`) | Add a new version and redeploy; revoke the old key at the provider |
| Database password | `.env` (`CADUCEUS_DB_PASSWORD`) locally; the secrets manager in production | See [postgres.md](postgres.md) |

Never paste a secret into a chat, an issue or a commit.

### Verify the audit log

As an auditor or administrator, open any case's history and choose "Check that nothing was changed", or call
`GET /api/audit/verify`. On Postgres, `python -m caduceus.postgres verify` checks the whole chain from the command
line. Run it after every restore from backup.

## When something goes wrong

### An identifier may have reached the external model

1. **Stop it.** Administration page: "Turn off the external model". This takes effect at once, for every case, and
   holds across restarts. Drafts are then written inside the network, or from the template.
2. **Keep the evidence.** Do not restart or redeploy yet. The audit log records every draft's route, model, the
   counts of identifiers removed, and a hash of what was sent; the case page's "See what was sent" shows the exact
   de-identified text.
3. **Tell the compliance officer** the same day. They decide whether it is a breach under HIPAA and who must be
   told. Do not decide that yourself.
4. **Find the cause.** Was a known identifier missed (a de-identification bug), or one the record did not know
   (free text)? Add the case to the leakage suite in `evals`, fix, and turn the route back on only when the gate
   passes and compliance agrees.

### The external model is down or slow

Nothing to do in a hurry. A draft that cannot be written by the model in time (`CADUCEUS_EXTERNAL_TIMEOUT`) is
written from the template, and the case says so. The demo starts from recorded replies and never waits on a model.

### The payer cannot be reached

Approved requests stay in the outbox, "Sending", and are tried again every few seconds; each failed attempt is a
`dispatch.error` event in the audit log. Specialists can still recall a request until it is sent. When the payer
answers again, the outbox empties on its own. If the outage will be long, tell the PA manager: the payer's
decision clock starts only when a request reaches it, so expedited requests may need a phone call.

### Cases cannot be prepared

The record source is down (the FHIR server, or the EHR in production), or the criteria set for the service does not
run. Check the policies page: a version whose fingerprint is "not the one the reviewer approved" does not run, by
design. Restore the approved file, or have the change reviewed and approved. Never edit `approved.lock.json` by
hand to make a version run.

### A payer publishes a different version of its policy

Approval is blocked on the affected cases, and each says so. The UM nurse compares the payer's version with the
approved one and approves a new version if the criteria changed. Nothing runs on an unreviewed policy.

### The audit log does not verify

Stop writing: scale the service to zero. Keep a copy of the database as it is. Compare it with the archived copy
(object lock, in production) to find the first event that differs, and tell the compliance officer and security.
The audit table refuses updates and deletes, so a break means someone had owner access: treat it as a security
incident.

### The demo deploy failed

Read the failed step in the `deploy-demo` run. A start-up failure leaves the last good revision serving, so the
site stays up. [The incident of October 30](incidents/2026-10-30-deploy-failed-at-startup.md) is a worked example.

## Who to call

| For | Role |
|---|---|
| Anything about patient information leaving the network | Compliance officer, then security |
| The workflow, the queue, a payer's behavior | PA manager |
| A criteria question, or a payer's new policy | UM nurse |
| The network, the database, the deployment | IT lead |
