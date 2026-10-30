#!/usr/bin/env bash
# Build the Synthea jar that make data runs, from source, with synthea.patch applied.
#   tools/synthea/build.sh .synthea/synthea.jar
#
# Upstream's published jar bundles library versions with known vulnerabilities (106 findings on 2026-10-30).
# synthea.patch moves them to fixed releases and changes nothing else; the dataset regenerating byte for byte
# is the check that the simulation is unchanged. Needs JDK 17+, git, and network access to GitHub, Gradle and
# Maven Central (the build is online; running Synthea afterwards is not, see scripts/offline.sh).
set -euo pipefail

SYNTHEA_REPO="https://github.com/synthetichealth/synthea"
SYNTHEA_COMMIT="bd8a52ba0a54ad8c6a1004b74787a14d8c25dbcc"  # master on 2026-10-21, the commit upstream's jar was built from
WRAPPER_SHA256="423cb469ccc0ecc31f0e4e1c309976198ccb734cdcbb7029d4bda0f18f57e8d9"  # gradle-wrapper.jar at that commit

out="$(realpath -m "${1:?usage: $0 path/to/synthea.jar}")"
here="$(cd "$(dirname "$0")" && pwd)"
src="$(dirname "$out")/synthea-src"
log="$(dirname "$out")/synthea-build.log"

annotate_failure() {
  # On CI, put the build's last lines on the run page (raw logs are not always at hand).
  if [ "${GITHUB_ACTIONS:-}" = true ] && [ -f "$log" ]; then
    grep -E "error:|warning: \[|FAILURE|What went wrong|Could not|> " "$log" | tail -n 40 \
      | python3 -c "import sys; t = sys.stdin.read().replace('%', '%25').replace('\r', '%0D').replace('\n', '%0A'); print('::error title=Synthea build::' + t)"
  fi
}
trap 'annotate_failure' ERR

rm -rf "$src" && mkdir -p "$src"
git -C "$src" init -q
git -C "$src" fetch -q --depth 1 "$SYNTHEA_REPO" "$SYNTHEA_COMMIT"
git -C "$src" -c advice.detachedHead=false checkout -q FETCH_HEAD
echo "$WRAPPER_SHA256  $src/gradle/wrapper/gradle-wrapper.jar" | sha256sum -c --quiet -
git -C "$src" apply "$here/synthea.patch"

(cd "$src" && ./gradlew --no-daemon --console=plain uberJar -x test) > "$log" 2>&1
cp "$src/build/libs/synthea-with-dependencies.jar" "$out"
echo "Built $out from $SYNTHEA_COMMIT with synthea.patch: sha256 $(sha256sum "$out" | cut -d' ' -f1)"
