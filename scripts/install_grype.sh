#!/usr/bin/env bash
# Install the Grype vulnerability scanner at a pinned release, refusing any download whose checksum differs.
# Grype rather than Trivy: Trivy's v0.69.4 release and trivy-action tags were hijacked in March 2026
# (GHSA-69fq-xp46-6x23). Whatever the scanner, pin the version and verify the bytes.
#   GRYPE_VERSION=0.120.1 GRYPE_SHA256=<sha256 of the linux_amd64 tarball> scripts/install_grype.sh
set -euo pipefail
: "${GRYPE_VERSION:?set GRYPE_VERSION}" "${GRYPE_SHA256:?set GRYPE_SHA256}"
tmp="$(mktemp -d)"
trap 'rm -rf "$tmp"' EXIT
curl -fsSL -o "$tmp/grype.tgz" \
  "https://github.com/anchore/grype/releases/download/v${GRYPE_VERSION}/grype_${GRYPE_VERSION}_linux_amd64.tar.gz"
echo "${GRYPE_SHA256}  $tmp/grype.tgz" | sha256sum -c -
tar -xzf "$tmp/grype.tgz" -C "$tmp" grype
sudo install -m 0755 "$tmp/grype" /usr/local/bin/grype
grype version
