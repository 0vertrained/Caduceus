#!/usr/bin/env bash
# Store the Ollama Cloud API key for the demo's draft model. Run it in Cloud Shell after setup-gcp.sh:
#
#   infra/demo/add-model-key.sh YOUR_PROJECT_ID
#
# It asks for the key without echoing it, so the key never appears on screen, in shell history or in a file. The
# Cloud Run service reads it at runtime, and the deploy workflow reads it to record the model's replies into the
# image. Ollama Cloud is a subscription with fixed limits, so the key can use up a quota but never run up a bill;
# the demo refuses any metered key. Run it again to replace the key.
set -euo pipefail

PROJECT="${1:?usage: $0 PROJECT_ID}"
SECRET="caduceus-model-api-key"
RUNTIME="caduceus-demo-runtime@${PROJECT}.iam.gserviceaccount.com"
DEPLOYER="caduceus-deployer@${PROJECT}.iam.gserviceaccount.com"

read -rsp "Ollama Cloud API key (from ollama.com/settings/keys): " key
echo
[ -n "$key" ] || { echo "No key entered; nothing changed." >&2; exit 1; }

gcloud config set project "$PROJECT" > /dev/null
gcloud secrets describe "$SECRET" > /dev/null 2>&1 ||
  gcloud secrets create "$SECRET" --replication-policy automatic > /dev/null
printf '%s' "$key" | gcloud secrets versions add "$SECRET" --data-file=- > /dev/null
unset key
for member in "$RUNTIME" "$DEPLOYER"; do
  gcloud secrets add-iam-policy-binding "$SECRET" --member "serviceAccount:${member}" \
    --role roles/secretmanager.secretAccessor > /dev/null
done
echo "Stored. The next deploy records the model's replies and gives the demo the key."
echo "To deploy now: GitHub > Actions > deploy-demo > Run workflow."
