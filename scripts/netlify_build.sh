#!/usr/bin/env bash
# Netlify's build for the public demo front end. Netlify runs it from ui/ (the base directory in netlify.toml).
# It writes the API's address into the proxy rule, then builds the UI. Netlify reads redirect rules after the
# build, so the address lives in the site's environment, not in the repository.
set -euo pipefail

config="$(cd "$(dirname "$0")/.." && pwd)/netlify.toml"
placeholder="https://CADUCEUS-API-ORIGIN.invalid"
origin="${CADUCEUS_API_ORIGIN:-}"
origin="${origin%/}"

if ! [[ "$origin" =~ ^https://[a-z0-9.-]+$ ]]; then
  echo "Set CADUCEUS_API_ORIGIN in Netlify to the API's address: https:// and a host name, nothing after it" >&2
  echo "(the Cloud Run service URL, for example https://caduceus-demo-123456789012.us-central1.run.app)." >&2
  exit 1
fi
if ! grep -qF "$placeholder" "$config"; then
  echo "No $placeholder in $config; was it already replaced, or did the proxy rule change?" >&2
  exit 1
fi

sed -i "s|$placeholder|$origin|g" "$config"
echo "API requests will be proxied to $origin"
npm run build
