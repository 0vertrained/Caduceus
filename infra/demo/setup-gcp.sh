#!/usr/bin/env bash
# One-time Google Cloud setup for the public demo's API. Run it in Cloud Shell, where gcloud is ready:
#
#   git clone https://github.com/0vertrained/Caduceus && cd Caduceus
#   infra/demo/setup-gcp.sh YOUR_PROJECT_ID [REGION]
#
# It creates:
#   - an Artifact Registry repository for the API image, keeping the five newest images
#   - a runtime identity for the Cloud Run service, allowed only to read the proxy signing key
#   - the proxy signing key itself, in Secret Manager (Netlify signs proxied requests with it)
#   - a deploy identity, and Workload Identity Federation so GitHub Actions running on main of this
#     repository, and nothing else, can deploy without a stored credential
#
# Safe to run again: anything that already exists is left as it is. Deploys happen from GitHub Actions
# (.github/workflows/deploy-demo.yml), whose env block holds the values printed at the end.
set -euo pipefail

PROJECT="${1:?usage: $0 PROJECT_ID [REGION]}"
REGION="${2:-us-central1}"
REPO="0vertrained/Caduceus"
REPO_ID="1439923656"  # GitHub's immutable id for $REPO: a renamed or recreated repository does not inherit access

SERVICE="caduceus-demo"
RUNTIME="caduceus-demo-runtime@${PROJECT}.iam.gserviceaccount.com"
DEPLOYER="caduceus-deployer@${PROJECT}.iam.gserviceaccount.com"
SECRET="caduceus-proxy-signing-key"

exists() { "$@" > /dev/null 2>&1; }
# A new service account can take a few seconds to be usable in a policy binding.
retry() { for _ in 1 2 3 4 5 6; do "$@" > /dev/null && return 0; sleep 5; done; "$@" > /dev/null; }
step() { printf '\n== %s\n' "$*"; }

gcloud config set project "$PROJECT" > /dev/null
NUMBER="$(gcloud projects describe "$PROJECT" --format='value(projectNumber)')"

step "Enabling the APIs the demo uses"
gcloud services enable run.googleapis.com artifactregistry.googleapis.com secretmanager.googleapis.com \
  iam.googleapis.com iamcredentials.googleapis.com sts.googleapis.com

step "Image repository"
exists gcloud artifacts repositories describe caduceus --location "$REGION" ||
  gcloud artifacts repositories create caduceus --repository-format docker --location "$REGION" \
    --description "Caduceus demo API images"
policy="$(mktemp)"
cat > "$policy" <<'JSON'
[
  {"name": "keep-five-newest", "action": {"type": "Keep"}, "mostRecentVersions": {"keepCount": 5}},
  {"name": "delete-week-old", "action": {"type": "Delete"}, "condition": {"olderThan": "604800s"}}
]
JSON
gcloud artifacts repositories set-cleanup-policies caduceus --location "$REGION" --policy "$policy" --no-dry-run \
  > /dev/null

step "Identities"
exists gcloud iam service-accounts describe "$RUNTIME" ||
  gcloud iam service-accounts create caduceus-demo-runtime --display-name "Caduceus demo API (runtime)"
exists gcloud iam service-accounts describe "$DEPLOYER" ||
  gcloud iam service-accounts create caduceus-deployer --display-name "Caduceus demo deploys from GitHub Actions"

step "Proxy signing key"
if ! exists gcloud secrets describe "$SECRET"; then
  head -c 48 /dev/urandom | base64 | tr -d '\n=' |
    gcloud secrets create "$SECRET" --replication-policy automatic --data-file=-
fi
retry gcloud secrets add-iam-policy-binding "$SECRET" --member "serviceAccount:${RUNTIME}" \
  --role roles/secretmanager.secretAccessor

step "What the deploy identity may do"
# Deploy Cloud Run services and make the demo public (setting its invoker policy needs run.admin),
# run them as the runtime identity, and push images to the one repository.
retry gcloud projects add-iam-policy-binding "$PROJECT" --member "serviceAccount:${DEPLOYER}" \
  --role roles/run.admin --condition None
retry gcloud iam service-accounts add-iam-policy-binding "$RUNTIME" --member "serviceAccount:${DEPLOYER}" \
  --role roles/iam.serviceAccountUser
retry gcloud artifacts repositories add-iam-policy-binding caduceus --location "$REGION" \
  --member "serviceAccount:${DEPLOYER}" --role roles/artifactregistry.writer

step "GitHub Actions sign-in (Workload Identity Federation)"
exists gcloud iam workload-identity-pools describe github --location global ||
  gcloud iam workload-identity-pools create github --location global --display-name "GitHub Actions"
exists gcloud iam workload-identity-pools providers describe caduceus --location global --workload-identity-pool github ||
  gcloud iam workload-identity-pools providers create-oidc caduceus --location global \
    --workload-identity-pool github --display-name "Caduceus deploys" \
    --issuer-uri "https://token.actions.githubusercontent.com" \
    --attribute-mapping "google.subject=assertion.sub,attribute.repository_id=assertion.repository_id,attribute.ref=assertion.ref" \
    --attribute-condition "assertion.repository_id == '${REPO_ID}' && assertion.ref == 'refs/heads/main'"
retry gcloud iam service-accounts add-iam-policy-binding "$DEPLOYER" --role roles/iam.workloadIdentityUser \
  --member "principalSet://iam.googleapis.com/projects/${NUMBER}/locations/global/workloadIdentityPools/github/attribute.repository_id/${REPO_ID}"

cat <<EOF

Done. Two more places to fill in (docs/demo-deployment.md walks through both).

GitHub, ${REPO}: the env block of .github/workflows/deploy-demo.yml (none of these is a secret)
  PROJECT                         ${PROJECT}
  REGION                          ${REGION}
  WORKLOAD_IDENTITY_PROVIDER      projects/${NUMBER}/locations/global/workloadIdentityPools/github/providers/caduceus
  DEPLOY_SA                       ${DEPLOYER}
  RUNTIME_SA                      ${RUNTIME}

Netlify: Site configuration > Environment variables
  CADUCEUS_API_ORIGIN             https://${SERVICE}-${NUMBER}.${REGION}.run.app
  CADUCEUS_PROXY_SIGNING_KEY      print it here with:
                                    gcloud secrets versions access latest --secret ${SECRET}
                                  paste it into Netlify only, and give it the Runtime scope
EOF
