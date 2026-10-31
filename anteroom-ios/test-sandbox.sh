#!/bin/bash
# Run on a Mac with Xcode and XcodeGen. No account, keys or paid services.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT/vendoo-studio"
PYTHONPATH=server VENDOO_STUDIO_PORT=4320 .venv/bin/python -m vendoo_studio.hosted_sandbox &
API_PID=$!
trap 'kill "$API_PID" 2>/dev/null || true; wait "$API_PID" 2>/dev/null || true' EXIT INT TERM
for _ in $(seq 1 120); do
  curl -sf http://localhost:4320/api/health >/dev/null && break
  kill -0 "$API_PID" 2>/dev/null || { echo 'Sandbox startup failed'; exit 1; }
  sleep .5
done
curl -fsS http://localhost:4320/api/health
cd "$ROOT/anteroom-ios"
xcodegen
xcodebuild test -project Anteroom.xcodeproj -scheme Anteroom \
  -destination "${ANTEROOM_TEST_DESTINATION:-platform=iOS Simulator,name=iPhone 17}" \
  -resultBundlePath "${ANTEROOM_TEST_RESULTS:-/tmp/anteroom-sandbox-$(date +%s).xcresult}" \
  CODE_SIGNING_ALLOWED=NO
