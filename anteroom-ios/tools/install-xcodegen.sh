#!/bin/bash
# Installs the pinned XcodeGen release after checking it against the checksum
# GitHub publishes for it, and puts it on the PATH of later CI steps.
# Update version, immutable asset ID and checksum together from the official release.
set -euo pipefail
version=2.46.0
asset_id=478866069
sha256=4d9e34b62172d645eed6457cac13fc222569974098ef4ee9c3368bedf0196806
dest="${1:-${RUNNER_TEMP:-$(mktemp -d)}/xcodegen-$version}"
mkdir -p "$dest"
# A proxy can surface HTTP 504 as curl receive error 56, which --retry alone
# does not retry. This is a bounded GET to a file; the pinned checksum below
# must still pass before anything is unpacked or executed.
# Shared Actions runners hit unauthenticated API rate limits (HTTP 403); the
# job token raises that limit. Local installs without a token keep working.
headers=(-H "Accept: application/octet-stream")
if [[ -n "${GITHUB_TOKEN:-}" ]]; then
  headers+=(-H "Authorization: Bearer ${GITHUB_TOKEN}")
fi
curl -fsSL --retry 3 --retry-all-errors --connect-timeout 15 --max-time 120 --retry-max-time 600 -o "$dest/xcodegen.zip" \
  "${headers[@]}" \
  "https://api.github.com/repos/yonaskolb/XcodeGen/releases/assets/$asset_id"
echo "$sha256  $dest/xcodegen.zip" | shasum -a 256 -c -
unzip -q -o "$dest/xcodegen.zip" -d "$dest"
"$dest/xcodegen/bin/xcodegen" --version
if [[ -n "${GITHUB_PATH:-}" ]]; then
  echo "$dest/xcodegen/bin" >> "$GITHUB_PATH"
fi
