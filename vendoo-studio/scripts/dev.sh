#!/bin/bash
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

if [[ ! -x .venv/bin/python || ! -d node_modules ]]; then
  echo "Run $ROOT/scripts/setup.sh first." >&2
  exit 1
fi

API_PORT="${VENDOO_STUDIO_PORT:-4318}"
WEB_PORT="${VENDOO_STUDIO_WEB_PORT:-5173}"
export VENDOO_STUDIO_PORT="$API_PORT" VENDOO_STUDIO_WEB_PORT="$WEB_PORT"

for port in "$API_PORT" "$WEB_PORT"; do
  if command -v lsof >/dev/null 2>&1 && lsof -nP -iTCP:"$port" -sTCP:LISTEN >/dev/null 2>&1; then
    echo "Port $port is already in use. Quit the other Anteroom process and try again." >&2
    exit 1
  fi
done

BACKEND_PID=""
FRONTEND_PID=""
cleanup() {
  if [[ -n "$FRONTEND_PID" ]] && kill -0 "$FRONTEND_PID" 2>/dev/null; then
    kill "$FRONTEND_PID" 2>/dev/null || true
  fi
  if [[ -n "$BACKEND_PID" ]] && kill -0 "$BACKEND_PID" 2>/dev/null; then
    kill "$BACKEND_PID" 2>/dev/null || true
  fi
}
trap cleanup EXIT INT TERM

export PYTHONPATH="$ROOT/server"
.venv/bin/python -m vendoo_studio.main &
BACKEND_PID=$!

npm run dev -- --host 127.0.0.1 --port "$WEB_PORT" --strictPort &
FRONTEND_PID=$!

echo "Anteroom backend  http://127.0.0.1:$API_PORT"
echo "Anteroom frontend http://127.0.0.1:$WEB_PORT"
echo "Open the frontend URL. Press Ctrl+C to stop both processes."

if [[ "${ANTEROOM_NO_OPEN:-}" != "1" ]] && command -v open >/dev/null 2>&1; then
  for _ in 1 2 3 4 5 6 7 8 9 10; do
    if curl -sf "http://127.0.0.1:$API_PORT/api/health" >/dev/null 2>&1; then
      break
    fi
    sleep 0.3
  done
  open "http://127.0.0.1:$WEB_PORT" >/dev/null 2>&1 || true
fi

wait
