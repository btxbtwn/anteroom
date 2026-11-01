#!/bin/bash
# Run the CI checks for what changed since origin/main (committed, staged,
# unstaged and untracked). Same commands as .github/workflows/ci.yml, except
# the secrets scan, which CI runs on a clean checkout.
#
#   ./check.sh           checks for changed components, browser tests included
#   ./check.sh --fast    skip the browser tests
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

changed() {
  [[ "$ALL" == "1" ]] || grep -qE "$1" <<<"$CHANGED"
}

PY="$ROOT/vendoo-studio/.venv/bin/python"
[[ -x "$PY" ]] || PY="$(command -v python3)"
BIN="$(dirname "$PY")"

FAILED=()
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

# Backend tests read frontend source, so any Anteroom change runs both suites.
if changed '^vendoo-studio/'; then
  run "Anteroom version and changelog" bash -c "'$PY' vendoo-studio/scripts/check-version-sync.py && '$PY' vendoo-studio/scripts/check-changelog.py"
  run "Anteroom frontend" bash -c "cd vendoo-studio && npm run lint && npm test && npm run build"
  run "Anteroom backend" bash -c "cd vendoo-studio && '$PY' -m ruff check server && VENDOO_STUDIO_DATA_DIR=\$(mktemp -d) '$PY' -m pytest -q -n auto"
  run "Anteroom code health" bash -c "cd vendoo-studio && npm run knip && '$BIN/lint-imports' && '$PY' scripts/check-dead-code.py && '$PY' scripts/check-design.py"
  run "Anteroom types" bash -c "cd vendoo-studio && CI=true '$PY' -m basedpyright"
  if [[ "$FAST" == "0" ]]; then
    run "Anteroom browser tests (sandbox)" bash -c "cd vendoo-studio && npm run e2e && npm run e2e:hosted"
  fi
fi

if changed '^anteroom-ios/'; then
  if [[ "$(uname)" == "Darwin" ]]; then
    run "iPhone sandbox UI tests" ./anteroom-ios/test-sandbox.sh
  else
    echo "iPhone sandbox UI tests require macOS and run in CI; unavailable on this machine."
  fi
fi

if changed '^vendoo-extension/'; then
  run "Extension" bash -c "
    git ls-files 'vendoo-extension/*.js' | grep -F -x -f <(printf '%s\n' \"\$CHANGED\") | xargs -r -n1 node --check
    node --test 'vendoo-extension/tests/*.test.js'
    '$PY' -m json.tool vendoo-extension/manifest.json >/dev/null
  "
fi

if changed '^webhook-relay/'; then
  run "Webhook relay" bash -c "cd webhook-relay && { [[ -d node_modules ]] || npm ci; } && npm test"
fi

if changed '^skills/'; then
  run "Skills" bash -c "test -f skills/list-this/SKILL.md && '$PY' -m json.tool skills/list-this/references/vendoo-dropdown-options.json >/dev/null"
fi

echo
if [[ ${#FAILED[@]} -gt 0 ]]; then
  printf 'Failed: %s\n' "${FAILED[@]}"
  exit 1
fi
echo "All checks passed."
