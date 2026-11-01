#!/usr/bin/env bash
# Sign in through Keycloak, end to end: docker compose's oidc profile (Keycloak with the mesaverde realm, the API in
# oidc mode, Postgres), then the OIDC walk at Keycloak's own pages. The passwords are made for the run and never
# shown; the access token is shortened to 70 seconds through Keycloak's admin API, so the walk sees one renewed.
#   scripts/e2e-keycloak.sh [screenshot-dir]
# Needs Docker with compose, and Node with the Playwright library and Chromium (as scripts/e2e.sh does). Uses ports
# 8000 and 8080 on 127.0.0.1, and removes its containers and volumes when it ends.
set -euo pipefail
cd "$(dirname "$0")/.."

out="${1:-ui/test-results/keycloak}"
rm -rf "$out" && mkdir -p "$out"
env_file="$(mktemp)"
compose=(docker compose --env-file "$env_file" --profile oidc)

secret() { openssl rand -hex 24; }
admin_password="$(secret)"
user_password="$(secret)"
secrets=("$admin_password" "$user_password" "$(secret)" "$(secret)")
cat > "$env_file" <<EOF
CADUCEUS_AUTH_MODE=oidc
CADUCEUS_KEYCLOAK_ADMIN_PASSWORD=${secrets[0]}
CADUCEUS_KEYCLOAK_USER_PASSWORD=${secrets[1]}
CADUCEUS_DEID_KEY=${secrets[2]}
CADUCEUS_DB_PASSWORD=${secrets[3]}
EOF
if [ -n "${GITHUB_ACTIONS:-}" ]; then
  for s in "${secrets[@]}"; do echo "::add-mask::$s"; done
fi

finish() {
  # The services' logs, for a failed run, with the run's passwords taken out first.
  "${compose[@]}" logs --no-color > "$out/compose.log" 2>&1 || true
  for s in "${secrets[@]}"; do sed -i "s/$s/[redacted]/g" "$out/compose.log"; done
  "${compose[@]}" down -v --remove-orphans > /dev/null 2>&1 || true
  rm -f "$env_file"
}
trap finish EXIT

for p in 8000 8080; do
  if curl -s -o /dev/null "http://127.0.0.1:$p/"; then
    echo "Port $p is in use; the realm's addresses need it free." >&2
    exit 1
  fi
done

"${compose[@]}" up --detach --build --wait --wait-timeout 600

keycloak=http://localhost:8080
admin_token="$(curl -fsS "$keycloak/realms/master/protocol/openid-connect/token" -d grant_type=password \
  -d client_id=admin-cli -d username=admin --data-urlencode "password=$admin_password" |
  python3 -c 'import json, sys; print(json.load(sys.stdin)["access_token"])')"
curl -fsS -X PUT "$keycloak/admin/realms/mesaverde" -H "Authorization: Bearer $admin_token" \
  -H "Content-Type: application/json" -d '{"accessTokenLifespan": 70}'

E2E_IDP=keycloak E2E_IDP_PASSWORD="$user_password" NODE_PATH="${NODE_PATH:-$(npm root -g)}" \
  node ui/e2e/oidc.mjs http://127.0.0.1:8000 "$keycloak/realms/mesaverde" "$out" | tee "$out/report.json"
