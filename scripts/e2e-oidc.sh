#!/usr/bin/env bash
# Sign in through OpenID Connect end to end: the test identity provider (tests/mock_idp.py), the API in oidc mode on
# a throwaway database, and the built UI in Chromium with its Content Security Policy enforced and axe-core checks.
#   scripts/e2e-oidc.sh [screenshot-dir]
# Needs what scripts/e2e.sh needs.
set -euo pipefail
cd "$(dirname "$0")/.."
export PYTHONPATH="$PWD/src${PYTHONPATH:+:$PYTHONPATH}"  # this checkout's code, as scripts/e2e.sh runs it

out="${1:-ui/test-results/oidc}"
port="${E2E_OIDC_PORT:-8767}"   # scripts/e2e.sh uses 8765 and scripts/demo_video.sh 8766, so all three can run
idp_port="${E2E_IDP_PORT:-8772}"
work="$(mktemp -d)"
trap 'kill "${server:-0}" "${idp:-0}" 2>/dev/null || true; rm -rf "$work"' EXIT

app="http://127.0.0.1:$port"
issuer="http://127.0.0.1:$idp_port/realms/mesaverde"

# Something else answering on either port would answer the walk's requests too.
for p in "$port" "$idp_port"; do
  if curl -s -o /dev/null "http://127.0.0.1:$p/"; then
    echo "Port $p is in use. Set E2E_OIDC_PORT or E2E_IDP_PORT to free ones." >&2
    exit 1
  fi
done

# Access tokens last 70 seconds, so the walk sees one renewed (a minute before it runs out).
python -m tests.mock_idp --port "$idp_port" --client-id caduceus-ui --audience caduceus-api \
  --redirect-uri "$app/auth/callback" --post-logout-redirect-uri "$app/signed-out" --access-token-seconds 70 &
idp=$!

export CADUCEUS_DB="$work/e2e.sqlite3"
export CADUCEUS_DEID_KEY="${CADUCEUS_DEID_KEY:-e2e-only-signing-key-at-least-32-bytes-long}"
export CADUCEUS_AUTH_MODE=oidc
export CADUCEUS_OIDC_ISSUER="$issuer"
export CADUCEUS_OIDC_JWKS_URL="$issuer/protocol/openid-connect/certs"
export CADUCEUS_OIDC_AUDIENCE=caduceus-api
export CADUCEUS_OIDC_CLIENT_ID=caduceus-ui

python -m caduceus.seed
uvicorn caduceus.api:create_app --factory --host 127.0.0.1 --port "$port" --log-level warning &
server=$!
for _ in $(seq 1 40); do
  curl -fsS "$app/api/auth-config" 2>/dev/null | grep -q '"oidc"' \
    && curl -fsS "$issuer/.well-known/openid-configuration" >/dev/null 2>&1 && break
  sleep 0.25
done

rm -rf "$out" && mkdir -p "$out"
NODE_PATH="${NODE_PATH:-$(npm root -g)}" node ui/e2e/oidc.mjs "$app" "$issuer" "$out" | tee "$out/report.json"
