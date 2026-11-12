#!/bin/bash
# Install Anteroom's Python packages exactly as uv.lock pins them, every file
# checked against its recorded hash, then the app itself without resolving
# anything. CI, the Mac app and the hosted deploy all install this way, so the
# audit and the tests see the versions that ship.
#
#   scripts/install-locked.sh <python> <extra>   e.g. .venv/bin/python dev
#
# Needs uv (https://docs.astral.sh/uv/). Refresh the lock with `uv lock`.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
PYTHON="${1:?usage: install-locked.sh <python> <extra>}"
EXTRA="${2:?usage: install-locked.sh <python> <extra>}"

if ! command -v uv >/dev/null 2>&1; then
  echo "uv is required to install from uv.lock: https://docs.astral.sh/uv/getting-started/installation/" >&2
  exit 1
fi

REQUIREMENTS="$(mktemp)"
trap 'rm -f "$REQUIREMENTS"' EXIT
# --frozen fails instead of re-resolving when pyproject.toml and uv.lock disagree.
# The build group is the locked build backend (setuptools) for the app itself.
uv export --project "$ROOT" --frozen --no-emit-project --extra "$EXTRA" --group build \
  --format requirements-txt --hashes --quiet --output-file "$REQUIREMENTS"
"$PYTHON" -m pip install --require-hashes --no-deps -r "$REQUIREMENTS"
# No build isolation: build with the locked setuptools just installed instead
# of fetching an unpinned one.
"$PYTHON" -m pip install --no-deps --no-build-isolation -e "$ROOT"
