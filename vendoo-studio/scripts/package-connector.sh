#!/bin/bash
# Build the separate menu-bar connector. Never publish or touch seller data.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
PYTHON="${PYTHON:-$ROOT/.venv/bin/python}"
DEVELOPMENT=0
if [[ "${1:-}" == "--development" ]]; then
  DEVELOPMENT=1
  export MACOS_SIGNING_IDENTITY=-
elif [[ $# != 0 ]]; then
  echo 'Usage: package-connector.sh [--development]' >&2
  exit 2
fi
if [[ "$DEVELOPMENT" == 0 && ( -z "${MACOS_SIGNING_IDENTITY:-}" || "$MACOS_SIGNING_IDENTITY" == '-' ) ]]; then
  echo 'Set MACOS_SIGNING_IDENTITY to the release certificate, or use --development for a local-only build.' >&2
  exit 1
fi
cd "$ROOT"
./scripts/install-locked.sh "$PYTHON" package
"$PYTHON" desktop/make_icon.py desktop/AppIcon.icns
"$PYTHON" -m PyInstaller --noconfirm --distpath "$ROOT/release" --workpath "$ROOT/build/connector" desktop/Connector.spec
APP="$ROOT/release/Anteroom Connector.app"
codesign --force --deep --sign "$MACOS_SIGNING_IDENTITY" "$APP"
codesign --verify --deep --strict "$APP"
if [[ "$DEVELOPMENT" == 0 ]]; then
  DETAILS="$(codesign -dv "$APP" 2>&1)"
  if [[ "$DETAILS" == *'Signature=adhoc'* || "$DETAILS" == *'not signed at all'* ]]; then
    echo 'The connector release needs certificate signing.' >&2
    exit 1
  fi
fi
# The smoke mode always makes its own temporary data folder and never reads credentials.
"$APP/Contents/MacOS/Anteroom Connector" --smoke
SUFFIX=""
if [[ "$DEVELOPMENT" == 1 ]]; then SUFFIX=-dev; fi
ZIP="$ROOT/release/Anteroom-Connector-macos$SUFFIX.zip"
ditto -c -k --keepParent "$APP" "$ZIP"
echo "$ZIP"
