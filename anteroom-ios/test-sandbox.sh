#!/bin/bash
# Run on a Mac with Xcode and XcodeGen. No account, keys or paid services.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT/vendoo-studio"
# The Debug app is built to reach the sandbox on 4320. If another checkout's
# sandbox already holds it, the health check below would pass against that
# server and the tests would run on someone else's data, so stop here.
if (exec 3<>/dev/tcp/127.0.0.1/4320) 2>/dev/null; then
  echo 'Port 4320 is already in use (another sandbox?). Stop it and run again.' >&2
  exit 1
fi
PYTHONPATH=server VENDOO_STUDIO_PORT=4320 .venv/bin/python -m vendoo_studio.hosted_sandbox &
API_PID=$!
trap 'kill "$API_PID" 2>/dev/null || true; wait "$API_PID" 2>/dev/null || true' EXIT INT TERM
# Cold CI runners can spend well over a minute on pgserver + seed before bind.
ready=
for _ in $(seq 1 360); do
  if curl -sf http://localhost:4320/api/health >/dev/null; then
    ready=1
    break
  fi
  kill -0 "$API_PID" 2>/dev/null || { echo 'Sandbox startup failed'; exit 1; }
  sleep .5
done
if [[ -z "$ready" ]]; then
  echo 'Sandbox did not become healthy in time'
  exit 1
fi
curl -fsS http://localhost:4320/api/health
cd "$ROOT/anteroom-ios"
xcodegen
# ANTEROOM_TEST_ONLY narrows the run, e.g. to AnteroomUITests/IPadSandboxTests on an iPad.
xcodebuild test -project Anteroom.xcodeproj -scheme Anteroom -only-testing:"${ANTEROOM_TEST_ONLY:-AnteroomUITests}" \
  -destination "${ANTEROOM_TEST_DESTINATION:-platform=iOS Simulator,name=iPhone 17}" \
  -resultBundlePath "${ANTEROOM_TEST_RESULTS:-/tmp/anteroom-sandbox-$(date +%s).xcresult}" \
  CODE_SIGNING_ALLOWED=NO
