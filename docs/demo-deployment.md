# Deploying the public demo

The demo runs as two pieces. Netlify serves the review UI and proxies `/api/*` to the API on Google Cloud Run.
Netlify signs every proxied request, and the API answers nothing else, so the Cloud Run address cannot be used to
get around the site. Netlify also answers API requests that did not come from the demo's pages itself, and limits
what one address can send ([Cost and limits](#cost-and-limits)).

```
visitor --> Netlify (UI, security headers) --signed /api/*--> Cloud Run (API, demo mode, 1 instance)
```

In demo mode the API serves the 11 synthetic cases, and three showcase cases built to show one thing each
(`data/showcase`), from memory with the development sign-in. Each visitor's
browser gets its own copy of the cases (a random id it keeps and sends with every request), so one visitor's clicks
never change what another sees. Copies are cloned in milliseconds from a seeded template, at most 60 are kept (the
least recently used goes first), and each starts over after 6 hours or when its visitor asks. A banner on every page
says the patients are synthetic, and a guided tour follows one case to the payer's approval, saying who to act as
at each step. The tour's phone link opens the same copy on another device. Drafts are written by a model on Ollama Cloud through the
PHI router's de-identified route, and each case page shows exactly what was sent. The deploy records the model's
replies into the image, so a cold start makes no calls; the running demo calls the model only for a request it has
not seen. Ollama Cloud is a subscription with fixed limits, so its key can use up a quota but never run up a bill,
and the demo refuses any metered key. Without a key the demo still runs, with drafts from the deterministic template.
Beside the API, in the same instance, runs the model inside the network: llama.cpp and Qwen2.5-1.5B-Instruct on
2 vCPU ([infra/model](../infra/model/Dockerfile)). It listens on 127.0.0.1, which nothing outside the instance can
reach, is built without an HTTPS client and runs with downloads refused, and writes the chart summary a clinician
sees, in the background, in about half a minute. It writes no drafts (`CADUCEUS_IN_VPC_DRAFTS=0`): on 2 vCPU a
draft would take minutes, so with the outside model switched off, the template writes them. Cloud Run gives both containers the instance's network, which the
API needs for Ollama Cloud, so the model's isolation rests on how it is built and run; in production it sits in a
subnet with no route out. The [model-image](../.github/workflows/model-image.yml) workflow builds it (about half an hour, only when
its folder changes), checks that it answers with no network at all, scans it, and publishes it; the deploy runs the
published one, and runs the API alone if there is none or Cloud Run refuses the pair, saying so on the run's page; the summary then comes from the record's facts.

A visitor can also open Caduceus from an EHR: the Help page links to the public SMART launcher, and the API reads the
launched sandbox patient's chart into that visitor's copy ([docs/smart-launch.md](smart-launch.md)). The API accepts
launches only from the SMART sandbox, and sends the browser back only to the site's own `/smart/callback`.

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
*deploy-demo* workflow can also be run by hand from the Actions tab. If the Netlify site has a different address, set
`SITE` in the same block: an EHR launch may return only to `$SITE/smart/callback`.

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
Manager. `MODEL_PREFERENCE` in `.github/workflows/deploy-demo.yml` ranks the models to use (Kimi K2.6 first, then
DeepSeek V3.2, GLM-5 and others); the deploy uses the first one Ollama Cloud offers the key, says which, and warns
with the list of offered models if none matches. Run the deploy-demo workflow afterwards.

## Checking it

- The site shows the demo banner, and signing in as Dolores Montoya shows the queue.
- The Cloud Run address on its own answers `/api/health` and refuses everything else with a 403. The deploy
  workflow checks this after every deploy.
- `curl -i https://caduceusdemo.netlify.app/api/health` gets a 404 from Netlify, saying the API answers only the
  demo's own pages, and does not start the server. `/api/auth-config` gets `{"mode":"demo"}` from the API.
- On a phone, sign in as Dr. Ana Reyes while the case waits for her signature.
- Help, then *Open the SMART launcher*: choose a patient and *Launch*, and Caduceus opens on that patient's record.

## Recording the walkthrough

The walkthrough at `/demo.mp4` and the README's stills are recorded by the
[walkthrough](../.github/workflows/walkthrough.yml) workflow, run by hand on main. It runs the API as the demo
does, with the published model image beside it on the runner's own loopback address, walks the demo in Chromium
with captions ([ui/e2e/video.mjs](../ui/e2e/video.mjs)), and pushes the video and stills to the `walkthrough`
branch. Watch them there before copying them to `ui/public/demo.mp4` and `docs/screenshots`. Without the model's
image, `scripts/demo_video.sh` records the same walk on any machine, with the summary from the record's facts.

## Cost and limits

- Cloud Run scales to zero when idle and is capped at one instance. Without the model, it bills only while a
  request is being handled. With the model, CPU stays on while the instance is up, so a summary can finish after
  its request returns; an instance stays at most 15 minutes after its last request. At 3 vCPU and 4 GiB, ten hours
  a month fits in the free tier, and heavy use costs a few dollars. Set a budget alert on the project anyway.
- Each visitor gets 300 requests and 60 changes per five minutes, and all visitors together get 5,000 requests per
  five minutes. Steps of an EHR launch, which make requests to the SMART sandbox, are capped apart: 10 per visitor
  and 200 for everyone per five minutes. One copy holds at most 3 patients from an EHR.
- Netlify answers an API request that did not come from one of the demo's pages itself, with a 404, so the
  request never starts the server. Every request a page makes carries the visitor's sandbox id, apart from the
  first, which asks how to sign in, and an edge function
  ([pages-only.js](../ui/netlify/edge-functions/pages-only.js)) runs on `/api` requests without one. It was added
  after 10 November 2026, when a scanner asked for 22 addresses it had read out of the pages' script, placeholders
  unfilled, in 18 seconds, and the first started the server. The end-to-end walk puts the same function in front of
  the API, so a page request it would not send on fails CI. It is a filter, not a lock: the header's name is in the
  script for anyone to read, and what a request may do is decided by the API as before. Two things still start the
  server without the id: `/api/auth-config`, the page's first request, and anything sent straight to the Cloud Run
  address, where the API answers the health check and refuses the rest.
- Netlify limits one address to 300 API requests a minute before it proxies them ([netlify.toml](../netlify.toml)).
  That is five times the pace of the API's own limit, so a visitor who overdoes it hears from the API, and only a
  flood meets Netlify's 429, which can take up to ten seconds to begin.
- [robots.txt](../ui/public/robots.txt) asks crawlers to stay out of `/api/`. The pages stay open to them, so a
  shared link still gets its preview.
- The demo refuses to start with a metered model API key (one billed per token, such as Anthropic's), so a public
  page can never run up a bill. It may hold a fixed-quota key, such as Ollama Cloud's.
- Netlify gives proxied requests 26 seconds; the API answers in well under one. The two slow exceptions stay inside
  it: reading a record from the EHR is cut off at 20 seconds, and a live draft falls back to the template after 20.
  The in-network model's summary never runs on a request: it is written in the background.
- The first request after idle waits for an instance to start, and that is the one request that can outlast the
  gateway. Measured from outside in November 2026: 7.7 seconds after eighteen idle minutes, and more than 26 after
  most of a day, when Netlify answered 504 once and the next request succeeded. The API's own work at
  start, importing and seeding from recorded replies, takes about three seconds on a development machine; the rest
  is the instance, which carries the model's container too. The page says so while it
  waits, offers the roster so the wait is spent choosing who to be, and keeps asking for 75 seconds
  ([App.tsx](../ui/src/App.tsx)). A warm instance would end the wait, at this instance's full price for every hour
  it stays up, since CPU stays on beside the model.

## Taking it down

Delete the Cloud Run service (`gcloud run services delete caduceus-demo --region us-central1`) and the Netlify
site, then remove `.github/workflows/deploy-demo.yml` (or disable it in the Actions tab) to stop further deploys.
