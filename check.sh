#!/bin/bash
# Run the CI checks for what changed since origin/main (committed, staged,
# unstaged and untracked). Same commands as .github/workflows/ci.yml, and the
# same rule for which components a change touches
# (.github/scripts/changed-components.py). Checks that need a tool this
# machine lacks (uv, gitleaks) say so and are skipped; CI still runs them.
#
#   ./check.sh           checks for changed components, browser tests and the
#                        Postgres run of the backend suite included
#   ./check.sh --fast    skip the browser tests and the Postgres run
#   ./check.sh --all     every component, whatever changed
set -uo pipefail

ROOT="$(cd "$(dirname "$0")" && pwd)"
cd "$ROOT"

ALL=0
FAST=0
for arg in "$@"; do
  case "$arg" in
    --all) ALL=1 ;;
    --fast) FAST=1 ;;
    *) echo "Unknown option: $arg" >&2; exit 2 ;;
  esac
done

BASE="$(git merge-base HEAD origin/main 2>/dev/null || echo HEAD)"
CHANGED="$( { git diff --name-only "$BASE"; git ls-files --others --exclude-standard; } | sort -u)"
export CHANGED

COMPONENTS="$(python3 .github/scripts/changed-components.py --stdin <<<"$CHANGED")" || exit 1
component() {
  [[ "$ALL" == "1" ]] || grep -qx "$1=true" <<<"$COMPONENTS"
}
changed() {
  [[ "$ALL" == "1" ]] || grep -qE "$1" <<<"$CHANGED"
}

PY="$ROOT/vendoo-studio/.venv/bin/python"
[[ -x "$PY" ]] || PY="$(command -v python3)"
BIN="$(dirname "$PY")"

FAILED=()
SKIPPED=()
skip() {
  echo
  echo "== $1"
  echo "-- $1: SKIPPED ($2)"
  SKIPPED+=("$1: $2")
}
run() {
  local name="$1"
  shift
  echo
  echo "== $name"
  if (set -e; "$@"); then
    echo "-- $name: ok"
  else
    echo "-- $name: FAILED"
    FAILED+=("$name")
  fi
}

# The secrets scan reads the commits on this branch and the uncommitted
# changes; CI also scans its clean checkout. Local data folders are not scanned.
if command -v gitleaks >/dev/null 2>&1; then
  run "Secrets" bash -c "
    gitleaks git . --log-opts='$BASE..HEAD' --redact --no-banner
    gitleaks git . --pre-commit --redact --no-banner
    gitleaks git . --pre-commit --staged --redact --no-banner
  "
else
  skip "Secrets" "gitleaks is not installed: https://github.com/gitleaks/gitleaks#installing"
fi

run "Anteroom version and changelog" bash -c "'$PY' vendoo-studio/scripts/check-version-sync.py && '$PY' vendoo-studio/scripts/check-changelog.py"

if component frontend; then
  run "Anteroom frontend" bash -c "cd vendoo-studio && npm run lint && npm run knip && npm test && npm run build"
  run "Anteroom hosted build" bash -c "cd vendoo-studio && ANTEROOM_HOSTED_BUILD=1 VITE_SUPABASE_URL=https://build-validation.supabase.co VITE_SUPABASE_PUBLISHABLE_KEY=public-build-validation npm run build"
fi

# Backend tests also inspect frontend, extension and skill sources. The shared
# selector retains those dependencies without rebuilding an unchanged frontend.
if component backend; then
  run "Anteroom backend" bash -c "cd vendoo-studio && VENDOO_STUDIO_DATA_DIR=\$(mktemp -d) '$PY' -m pytest -q -n auto"
  if [[ "$FAST" == "1" ]]; then
    skip "Anteroom backend on Postgres" "--fast"
  elif [[ -n "${ANTEROOM_TEST_DATABASE_URL:-}" ]]; then
    run "Anteroom backend on Postgres" bash -c "cd vendoo-studio && VENDOO_STUDIO_DATA_DIR=\$(mktemp -d) '$PY' -m pytest -q -n auto"
  else
    # pgserver (a dev dependency) runs a private Postgres 16 for the run.
    run "Anteroom backend on Postgres" bash -c "cd vendoo-studio && '$PY' - <<'PY'
import os, subprocess, sys, tempfile
from pathlib import Path
from pgserver.postgres_server import get_server
with tempfile.TemporaryDirectory(prefix='anteroom-check-postgres-') as root:
    server = get_server(Path(root) / 'postgres', cleanup_mode='delete')
    env = {**os.environ, 'ANTEROOM_TEST_DATABASE_URL': server.get_uri(), 'VENDOO_STUDIO_DATA_DIR': str(Path(root) / 'data')}
    sys.exit(subprocess.call([sys.executable, '-m', 'pytest', '-q', '-n', 'auto'], env=env))
PY"
  fi
fi

if component health; then
  run "Anteroom code health" bash -c "cd vendoo-studio && '$PY' -m ruff check server && '$BIN/lint-imports' && '$PY' scripts/check-dead-code.py && '$PY' scripts/check-design.py"
  run "Anteroom types" bash -c "cd vendoo-studio && CI=true '$PY' -m basedpyright"
fi

if component audit; then
  if command -v uv >/dev/null 2>&1; then
    run "Anteroom Python lock and audit" bash -c "
      cd vendoo-studio && uv lock --check
      locked=\$(mktemp)
      uv export --frozen --no-emit-project --all-extras --all-groups --format requirements-txt --hashes --quiet --output-file \"\$locked\"
      '$PY' -m pip_audit --disable-pip --require-hashes -r \"\$locked\"
    "
  else
    skip "Anteroom Python lock and audit" "uv is not installed: https://docs.astral.sh/uv/getting-started/installation/"
  fi
fi

if component browser; then
  if [[ "$FAST" == "0" ]]; then
    run "Anteroom browser tests (sandbox)" bash -c "cd vendoo-studio && node scripts/check-bridge-network.mjs && node scripts/check-bridge-connection.mjs && npm run e2e && npm run e2e:hosted"
  else
    skip "Anteroom browser tests" "--fast"
  fi
fi

if changed '^(anteroom-ios|contracts)/'; then
  if [[ "$(uname)" == "Darwin" ]]; then
    run "iPhone sandbox UI tests" ./anteroom-ios/test-sandbox.sh
  else
    echo "iPhone sandbox UI tests require macOS and run in CI; unavailable on this machine."
  fi
fi

if component extension; then
  run "Extension" bash -c "
    vendoo-studio/node_modules/.bin/oxlint -c vendoo-extension/.oxlintrc.json --deny-warnings vendoo-extension
    git ls-files 'vendoo-extension/*.js' | grep -F -x -f <(printf '%s\n' \"\$CHANGED\") | xargs -r -n1 node --check
    node --test 'vendoo-extension/tests/*.test.js'
    '$PY' -m json.tool vendoo-extension/manifest.json >/dev/null
    node vendoo-extension/tools/version-bump/check.js '$BASE'
  "
fi

if component relay; then
  run "Webhook relay" bash -c "cd webhook-relay && { [[ -d node_modules ]] || npm ci; } && npm test"
fi

if changed '^skills/'; then
  run "Skills" bash -c "test -f skills/list-this/SKILL.md && '$PY' -m json.tool skills/list-this/references/vendoo-dropdown-options.json >/dev/null"
fi

echo
if [[ ${#SKIPPED[@]} -gt 0 ]]; then
  printf 'Skipped: %s\n' "${SKIPPED[@]}"
fi
if [[ ${#FAILED[@]} -gt 0 ]]; then
  printf 'Failed: %s\n' "${FAILED[@]}"
  exit 1
fi
echo "All checks passed."
