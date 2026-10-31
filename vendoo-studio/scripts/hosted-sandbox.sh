#!/bin/bash
# Hosted workspace and iPhone API on disposable Postgres; no accounts or keys.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"
export VENDOO_STUDIO_PORT="${VENDOO_STUDIO_PORT:-4320}"
export VENDOO_STUDIO_WEB_PORT="${VENDOO_STUDIO_WEB_PORT:-5175}"
export VITE_SUPABASE_URL="http://127.0.0.1:$VENDOO_STUDIO_PORT/local"
export VITE_SUPABASE_PUBLISHABLE_KEY=sandbox-public
export VITE_ANTEROOM_SANDBOX=1
PYTHONPATH="$ROOT/server" .venv/bin/python -m vendoo_studio.hosted_sandbox &
API_PID=$!
FRONTEND_PID=''
cleanup() {
  kill "$API_PID" ${FRONTEND_PID:+"$FRONTEND_PID"} 2>/dev/null || true
  wait "$API_PID" ${FRONTEND_PID:+"$FRONTEND_PID"} 2>/dev/null || true
}
trap cleanup EXIT INT TERM
for _ in $(seq 1 120); do
  curl -sf "http://127.0.0.1:$VENDOO_STUDIO_PORT/api/health" >/dev/null && break
  kill -0 "$API_PID" 2>/dev/null || { echo 'Hosted sandbox exited during startup'; exit 1; }
  sleep 0.5
done
curl -sf "http://127.0.0.1:$VENDOO_STUDIO_PORT/api/health" >/dev/null
npm run dev -- --host 127.0.0.1 --port "$VENDOO_STUDIO_WEB_PORT" --strictPort &
FRONTEND_PID=$!
echo "Hosted workspace: http://127.0.0.1:$VENDOO_STUDIO_WEB_PORT/cloud.html"
echo "iPhone API: http://127.0.0.1:$VENDOO_STUDIO_PORT (Debug ANTEROOM_SANDBOX_URL)"
wait "$API_PID"
