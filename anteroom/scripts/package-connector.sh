#!/bin/bash
# Build the separate menu-bar connector. Never publish or touch seller data.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
PYTHON="${PYTHON:-$ROOT/.venv/bin/python}"
DEVELOPMENT=0
PREBUILT=0
while [[ $# -gt 0 ]]; do
  case "$1" in
    --development)
      DEVELOPMENT=1
      export MACOS_SIGNING_IDENTITY=-
      shift
      ;;
    --prebuilt)
      PREBUILT=1
      shift
      ;;
    *)
      echo 'Usage: package-connector.sh [--development] [--prebuilt]' >&2
      exit 2
      ;;
  esac
done
if [[ "$DEVELOPMENT" == 0 && ( -z "${MACOS_SIGNING_IDENTITY:-}" || "$MACOS_SIGNING_IDENTITY" == '-' ) ]]; then
  echo 'Set MACOS_SIGNING_IDENTITY to the release certificate, or use --development for a local-only build.' >&2
  exit 1
fi
cd "$ROOT"
if [[ "$PREBUILT" == 0 ]]; then
  ./scripts/install-locked.sh "$PYTHON" package
fi
"$PYTHON" desktop/make_icon.py desktop/AppIcon.icns
"$PYTHON" -m PyInstaller --noconfirm --distpath "$ROOT/release" --workpath "$ROOT/build/connector" desktop/Connector.spec
APP="$ROOT/release/Anteroom Connector.app"
# Inside-out, with the hardened runtime and a secure timestamp, like the app.
"$PYTHON" scripts/sign_macos_bundle.py "$APP" "$MACOS_SIGNING_IDENTITY"
if [[ "$DEVELOPMENT" == 0 ]]; then
  DETAILS="$(codesign -dv "$APP" 2>&1)"
  if [[ "$DETAILS" == *'Signature=adhoc'* || "$DETAILS" == *'not signed at all'* ]]; then
    echo 'The connector release needs certificate signing.' >&2
    exit 1
  fi
fi
# The smoke mode always makes its own temporary data folder and never reads
# credentials. Run under the hardened runtime, it also proves the signature's
# entitlements are enough for the bundled Python.
"$APP/Contents/MacOS/Anteroom Connector" --smoke
if [[ "$DEVELOPMENT" == 0 ]]; then
  # Sellers download the connector directly, so Gatekeeper must accept it.
  # Skipped (with a log line) without an App Store Connect key.
  ./scripts/notarize-macos-app.sh "$APP"
fi
SUFFIX=""
if [[ "$DEVELOPMENT" == 1 ]]; then SUFFIX=-dev; fi
ZIP="$ROOT/release/Anteroom-Connector-macos$SUFFIX.zip"
ditto -c -k --keepParent "$APP" "$ZIP"
echo "$ZIP"
