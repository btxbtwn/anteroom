#!/bin/bash
# Run Playwright's two file-level shards with one sandbox per shard.
set -euo pipefail
cd "$(dirname "$0")/.."

output="${1:?usage: check-browser-shards.sh OUTPUT_DIR [--serial]}"
if [[ -n "${2:-}" && "$2" != "--serial" ]]; then
  echo "Unknown argument: $2" >&2
  exit 2
fi
mkdir -p "$output"
output="$(cd "$output" && pwd)"
umask 077
evidence="$output/evidence"
mkdir "$evidence"
node -e 'process.stdout.write(require("node:crypto").randomBytes(32))' > "$evidence/identity-key"
reporter=../.github/scripts/browser-evidence-reporter.mjs
export CI=true
SANDBOX_BROWSER_EVIDENCE_FILE="$evidence/full.json" \
  npm run e2e -- --list --reporter="$reporter" --output="$output/list"

api_port="${ANTEROOM_PORT:-4329}"
web_port="${ANTEROOM_WEB_PORT:-5184}"
run_shard() {
  local shard="$1"
  ANTEROOM_PORT="$((api_port + shard - 1))" \
  ANTEROOM_WEB_PORT="$((web_port + shard - 1))" \
  ANTEROOM_SANDBOX_DIR="$output/data-$shard" \
  VITE_CACHE_DIR="$output/vite-$shard" \
  PLAYWRIGHT_HTML_OUTPUT_DIR="$output/report-$shard" \
  SANDBOX_BROWSER_EVIDENCE_FILE="$evidence/shard-$shard.json" \
    npm run e2e -- --shard="$shard/2" --output="$output/results-$shard"
}

status=0
if [[ "${2:-}" == "--serial" ]]; then
  run_shard 1 || status=$?
  run_shard 2 || status=$?
else
  run_shard 1 &
  first=$!
  run_shard 2 &
  second=$!
  trap 'kill "$first" "$second" 2>/dev/null || true' INT TERM
  wait "$first" || status=$?
  wait "$second" || status=$?
fi
if [[ "$status" != 0 ]]; then exit "$status"; fi
node ../.github/scripts/verify-browser-evidence.mjs "$evidence"
