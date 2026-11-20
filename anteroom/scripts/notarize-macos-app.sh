#!/bin/bash
# Notarize a signed app with Apple and staple the ticket to it.
#
#   notarize-macos-app.sh path/to/App.app
#
# Needs an App Store Connect API key: NOTARY_API_KEY_PATH (the .p8 file),
# NOTARY_API_KEY_ID and NOTARY_API_ISSUER_ID. Without them it skips with a log
# line, unless MACOS_REQUIRE_NOTARIZATION=1 (release builds), where a missing
# key or a rejected submission fails: a release must not quietly go back to
# needing the Control-click workaround.
set -euo pipefail

APP="${1:?Usage: notarize-macos-app.sh path/to/App.app}"
NAME="$(basename "$APP")"
PYTHON="${PYTHON:-python3}"

fail() {
  local message="$1"
  if [[ -n "${GITHUB_ACTIONS:-}" ]]; then
    echo "::error title=Notarization failed for $NAME::$message"
  fi
  echo "Notarization failed for $NAME: $message" >&2
  exit 1
}

if [[ -z "${NOTARY_API_KEY_PATH:-}" ]]; then
  if [[ "${MACOS_REQUIRE_NOTARIZATION:-0}" == 1 ]]; then
    fail "No App Store Connect API key. Add NOTARY_API_KEY_P8, NOTARY_API_KEY_ID and NOTARY_API_ISSUER_ID to the release environment (Settings → Environments → release), then re-run this workflow."
  fi
  echo "Skipping notarization of $NAME: no App Store Connect API key in this build (pull requests and local builds are never notarized)."
  exit 0
fi
if [[ ! -s "$NOTARY_API_KEY_PATH" || -z "${NOTARY_API_KEY_ID:-}" || -z "${NOTARY_API_ISSUER_ID:-}" ]]; then
  fail "NOTARY_API_KEY_PATH, NOTARY_API_KEY_ID and NOTARY_API_ISSUER_ID must all be set. Check the three NOTARY_API_* secrets in the release environment, then re-run this workflow."
fi

AUTH=(--key "$NOTARY_API_KEY_PATH" --key-id "$NOTARY_API_KEY_ID" --issuer "$NOTARY_API_ISSUER_ID")
WORK="$(mktemp -d)"
trap 'rm -rf "$WORK"' EXIT
# notarytool takes a zip, dmg or pkg. The ticket is stapled to the app itself
# afterwards, so the zip sent to Apple is thrown away.
ditto -c -k --sequesterRsrc --keepParent "$APP" "$WORK/upload.zip"

echo "Submitting $NAME to Apple's notary service (usually 2-10 minutes)…"
SUBMIT_STATUS=0
xcrun notarytool submit "$WORK/upload.zip" "${AUTH[@]}" \
  --wait --timeout 40m --output-format json > "$WORK/submit.json" 2> "$WORK/submit.err" || SUBMIT_STATUS=$?
cat "$WORK/submit.json"
cat "$WORK/submit.err" >&2
read -r SUBMISSION_ID STATUS < <("$PYTHON" -c '
import json, sys
try:
    data = json.load(open(sys.argv[1]))
except (OSError, ValueError):
    data = {}
print(data.get("id") or "-", data.get("status") or "-")
' "$WORK/submit.json")

if [[ "$STATUS" != "Accepted" ]]; then
  if [[ "$SUBMISSION_ID" != "-" ]]; then
    echo "Apple's log for submission $SUBMISSION_ID:"
    xcrun notarytool log "$SUBMISSION_ID" "${AUTH[@]}" || true
    fail "Apple answered '$STATUS' (submission $SUBMISSION_ID). Nothing was published. Read Apple's log just above: each issue names a file and why (commonly code signed without the hardened runtime or a timestamp, or with a certificate that is not Developer ID Application). Fix that in scripts/sign_macos_bundle.py, then push. If the log shows no issues, re-run the workflow."
  fi
  fail "notarytool exited with $SUBMIT_STATUS before Apple accepted the upload. Nothing was published. A 401 or 'invalid credentials' means the API key is wrong or revoked: create a new Team key with Developer access in App Store Connect → Users and Access → Integrations, update NOTARY_API_KEY_P8 / NOTARY_API_KEY_ID / NOTARY_API_ISSUER_ID in the release environment, and re-run. A message about agreements means the Account Holder must accept the latest agreement at developer.apple.com. Otherwise re-run the workflow; Apple's service may have been down."
fi

xcrun stapler staple "$APP"
xcrun stapler validate "$APP"
# What Gatekeeper decides for a download of this app on a new Mac.
ASSESSMENT="$(spctl --assess --type execute --verbose=4 "$APP" 2>&1)" || {
  echo "$ASSESSMENT" >&2
  fail "Gatekeeper rejects the stapled app. Nothing was published."
}
echo "$ASSESSMENT"
if [[ "$ASSESSMENT" != *"Notarized Developer ID"* ]]; then
  fail "Gatekeeper does not see a notarized Developer ID app. Nothing was published."
fi
echo "Notarized and stapled $NAME (submission $SUBMISSION_ID)."
