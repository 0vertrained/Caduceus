# Deploying the public demo

The demo runs as two pieces. Netlify serves the review UI and proxies `/api/*` to the API on Google Cloud Run.
Netlify signs every proxied request, and the API answers nothing else, so the Cloud Run address cannot be used to
get around the site.

```
visitor --> Netlify (UI, security headers) --signed /api/*--> Cloud Run (API, demo mode, 1 instance)
```

In demo mode the API serves the 11 synthetic cases from memory with the development sign-in. Each visitor's
browser gets its own copy of the cases (a random id it keeps and sends with every request), so one visitor's clicks
never change what another sees. Copies are cloned in milliseconds from a seeded template, at most 60 are kept (the
least recently used goes first), and each starts over after 6 hours or when its visitor asks. A banner on every page
says the patients are synthetic, and a guided tour follows one case to the payer's approval, saying who to act as
at each step. The tour's phone link opens the same copy on another device. Drafts are written by a model on Ollama Cloud through the
PHI router's de-identified route, and each case page shows exactly what was sent. The deploy records the model's
replies into the image, so a cold start makes no calls; the running demo calls the model only for a request it has
not seen. Ollama Cloud is a subscription with fixed limits, so its key can use up a quota but never run up a bill,
and the demo refuses any metered key. Without a key the demo still runs, with drafts from the deterministic template.
There is no in-VPC model in the demo, so the chart summary a clinician sees comes from the template too.

## One-time setup

**1. Google Cloud.** Create a project with billing enabled, then open Cloud Shell and run:

```
git clone https://github.com/0vertrained/Caduceus && cd Caduceus
infra/demo/setup-gcp.sh YOUR_PROJECT_ID us-central1
```

The script prints the values for the next two steps. It is safe to run again.

**2. GitHub.** Put the five values the script printed into the `env` block of
`.github/workflows/deploy-demo.yml` (`PROJECT`, `REGION`, `WORKLOAD_IDENTITY_PROVIDER`, `DEPLOY_SA`,
`RUNTIME_SA`) and push. None of them is a secret: Google accepts GitHub's token only from this repository, by its
immutable id, running on `main`. From then on the API deploys by itself whenever CI passes on `main`; the
*deploy-demo* workflow can also be run by hand from the Actions tab.

**3. Netlify.** *Add new site > Import an existing project*, and pick this repository; `netlify.toml` supplies the
build settings. Under *Site configuration > Environment variables*, add:

| Variable | Value | Settings |
|---|---|---|
| `CADUCEUS_API_ORIGIN` | the `https://caduceus-demo-....run.app` address the script printed | Not secret; scope Builds |
| `CADUCEUS_PROXY_SIGNING_KEY` | the output of `gcloud secrets versions access latest --secret caduceus-proxy-signing-key` | *Contains secret values*; scope Runtime only; context Production only |

For the signing key:

- *Contains secret values* makes it write-only, and turns on Netlify's secret scanning, which fails any deploy whose
  repository or build output contains the key.
- Runtime is the only scope it needs, because Netlify signs at request time. Leaving out Builds means no build step,
  and no package installed during a build, can read it. That includes the deploy previews Netlify builds for
  Dependabot's pull requests.
- With Production as the only context, preview deploys send no signature, so the API refuses their `/api` calls.
- Give it no `dev` value: values in the dev context stay readable.

Copy the key straight from Cloud Shell into Netlify; it never needs to be anywhere else. Trigger a deploy.

**4. The draft model (optional).** Create an API key at ollama.com/settings/keys, then in Cloud Shell run
`infra/demo/add-model-key.sh YOUR_PROJECT_ID`. It asks for the key without echoing it and stores it in Secret
Manager. The model is set by `EXTERNAL_MODEL` in `.github/workflows/deploy-demo.yml` (`gpt-oss:120b`); the deploy
warns, listing what Ollama Cloud offers, if that name is not available. Run the deploy-demo workflow afterwards.

## Checking it

- The site shows the demo banner, and signing in as Dolores Montoya shows the queue.
- The Cloud Run address on its own answers `/api/health` and refuses everything else with a 403. The deploy
  workflow checks this after every deploy.
- On a phone, sign in as Dr. Ana Reyes while the case waits for her signature.

## Cost and limits

- Cloud Run bills only while a request is being handled, scales to zero when idle, and is capped at one instance.
  A demo that is visited now and then fits in the free tier. Set a budget alert on the project anyway.
- Each visitor gets 300 requests and 60 changes per five minutes, and all visitors together get 5,000 requests per
  five minutes.
- The demo refuses to start with a model API key configured, so a public page can never spend model credits.
- Netlify gives proxied requests 26 seconds; the API answers in well under one.

## Taking it down

Delete the Cloud Run service (`gcloud run services delete caduceus-demo --region us-central1`) and the Netlify
site, then remove `.github/workflows/deploy-demo.yml` (or disable it in the Actions tab) to stop further deploys.
