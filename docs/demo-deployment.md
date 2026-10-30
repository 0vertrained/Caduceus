# Deploying the public demo

The demo runs as two pieces. Netlify serves the review UI and proxies `/api/*` to the API on Google Cloud Run.
Netlify signs every proxied request, and the API answers nothing else, so the Cloud Run address cannot be used to
get around the site.

```
visitor --> Netlify (UI, security headers) --signed /api/*--> Cloud Run (API, demo mode, 1 instance)
```

In demo mode the API serves the 11 synthetic cases from memory with the development sign-in. It goes back to a
clean queue every 6 hours, on request (at most once a minute), and whenever Cloud Run starts a fresh instance. A
banner on every page says the patients are synthetic. No model is configured, so drafts come from the
deterministic template; to show the PHI router with a real model, run it locally with
`docker compose --profile llm`.

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
