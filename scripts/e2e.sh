#!/usr/bin/env bash
# Seed a throwaway database, serve the built UI and API, walk the real screens with axe-core, then stop.
#   scripts/e2e.sh [screenshot-dir]
# Needs: the Python package installed, ui/dist built, and Node with the Playwright library + Chromium.
set -euo pipefail
cd "$(dirname "$0")/.."

out="${1:-ui/test-results}"
port="${E2E_PORT:-8765}"
work="$(mktemp -d)"
trap 'kill "${server:-0}" 2>/dev/null || true; rm -rf "$work"' EXIT

export CADUCEUS_DB="$work/e2e.sqlite3"
export CADUCEUS_DEID_KEY="${CADUCEUS_DEID_KEY:-e2e-only-signing-key-at-least-32-bytes-long}"
export CADUCEUS_AUTH_MODE=dev

python -m caduceus.seed
uvicorn caduceus.api:create_app --factory --host 127.0.0.1 --port "$port" --log-level warning &
server=$!
for _ in $(seq 1 40); do
  curl -fsS "http://127.0.0.1:$port/api/health" >/dev/null 2>&1 && break
  sleep 0.25
done

rm -rf "$out" && mkdir -p "$out"
NODE_PATH="${NODE_PATH:-$(npm root -g)}" node ui/e2e/run.mjs "http://127.0.0.1:$port" "$out" | tee "$out/report.json"
