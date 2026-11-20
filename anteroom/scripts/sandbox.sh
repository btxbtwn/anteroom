#!/bin/bash
# Run Anteroom offline on throwaway data: fake AI provider, in-memory keychain,
# no outbound network, and publishing refused. See anteroom/sandbox.py.
#
#   ./scripts/sandbox.sh            API on 4319, UI on 5174, fresh data each run
#   ./scripts/sandbox.sh --keep     reuse the data folder from the last run
#   ./scripts/sandbox.sh --empty    start with no sample listings
#
# Ports and data folder can be overridden with ANTEROOM_PORT,
# ANTEROOM_WEB_PORT and ANTEROOM_SANDBOX_DIR. The extension talks to 4318,
# so a sandbox on any other port never receives it.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

KEEP=0
SEED=1
for arg in "$@"; do
  case "$arg" in
    --hosted) exec "$ROOT/scripts/hosted-sandbox.sh" ;;
    --keep) KEEP=1 ;;
    --empty) SEED=0 ;;
    *) echo "Unknown option: $arg" >&2; exit 2 ;;
  esac
done

export ANTEROOM_SANDBOX=1
export ANTEROOM_PORT="${ANTEROOM_PORT:-4319}"
export ANTEROOM_WEB_PORT="${ANTEROOM_WEB_PORT:-5174}"
if [[ "$ANTEROOM_PORT" == "4318" ]]; then
  echo "The sandbox cannot use 4318: that is the real Anteroom's port, and the extension connects to it." >&2
  exit 1
fi

DATA_DIR="${ANTEROOM_SANDBOX_DIR:-${TMPDIR:-/tmp}/anteroom-sandbox-$ANTEROOM_PORT}"
if [[ "$KEEP" == "0" ]]; then
  rm -rf "$DATA_DIR"
fi
mkdir -p "$DATA_DIR"
export ANTEROOM_DATA_DIR="$DATA_DIR"
export ANTEROOM_NO_OPEN="${ANTEROOM_NO_OPEN:-1}"

"$ROOT/scripts/dev.sh" &
DEV_PID=$!
trap 'kill "$DEV_PID" 2>/dev/null || true; wait "$DEV_PID" 2>/dev/null || true' EXIT INT TERM

API="http://127.0.0.1:$ANTEROOM_PORT"
for _ in $(seq 1 120); do
  curl -sf "$API/api/health" >/dev/null 2>&1 && break
  kill -0 "$DEV_PID" 2>/dev/null || { echo "Anteroom exited during startup." >&2; exit 1; }
  sleep 0.5
done
curl -sf "$API/api/health" >/dev/null || { echo "Anteroom did not answer on $API." >&2; exit 1; }

if [[ "$SEED" == "1" && "$KEEP" == "0" ]]; then
  PYTHONPATH="$ROOT/server" .venv/bin/python -m anteroom.sandbox_seed "$API"
fi

echo "Sandbox ready: http://127.0.0.1:$ANTEROOM_WEB_PORT (API $API, data $DATA_DIR)"
wait "$DEV_PID"
