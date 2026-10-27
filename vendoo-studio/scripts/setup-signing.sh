#!/bin/bash
# Give Anteroom's release builds a signing certificate macOS trusts across
# updates, so an update stops asking for your Mac password.
#
# Why: macOS Keychain recognises later versions of an app by its Apple Team
# ID. A certificate Apple did not issue (the self-signed one Anteroom used
# first) has no Team ID, so Keychain ties your saved sign-ins to one exact
# build and asks again after every update. A Developer ID Application
# certificate carries a Team ID, and every build signed with it is trusted.
#
# Once, before running this:
#   1. Join the Apple Developer Program (developer.apple.com/programs).
#   2. In Xcode: Settings → Accounts → your team → Manage Certificates → + →
#      Developer ID Application. (Or create it on developer.apple.com →
#      Certificates and open the downloaded .cer.)
#   3. In Keychain Access → login → My Certificates, right-click
#      "Developer ID Application: …" → Export… → save as a .p12 with a password.
#
# Then:  ./scripts/setup-signing.sh ~/Desktop/DeveloperID.p12
#
# The script checks the file, uploads it and its password as the GitHub
# Actions secrets MACOS_SIGNING_CERT_P12_BASE64 and MACOS_SIGNING_CERT_PASSWORD,
# and leaves nothing behind. Delete the .p12 afterwards; Keychain still has it.
set -euo pipefail

REPO="${REPO:-btxbtwn/list-this-vendoo-suite}"
P12="${1:-}"
if [[ -z "$P12" || ! -f "$P12" ]]; then
  echo "Usage: $0 path/to/certificate.p12" >&2
  exit 1
fi
if ! gh auth status >/dev/null 2>&1; then
  echo "Sign in to GitHub first: gh auth login" >&2
  exit 1
fi

read -r -s -p "Password you set when exporting the .p12: " P12_PASSWORD
echo

# Import into a throwaway keychain, the way the release build does, so a file
# that would fail in CI fails here instead. Your login keychain is not touched.
WORK="$(mktemp -d)"
KEYCHAIN="$WORK/check.keychain-db"
SEARCH_LIST="$(security list-keychains -d user | tr -d '"')"
cleanup() {
  security delete-keychain "$KEYCHAIN" >/dev/null 2>&1 || true
  # shellcheck disable=SC2086
  security list-keychains -d user -s $SEARCH_LIST >/dev/null 2>&1 || true
  rm -rf "$WORK"
}
trap cleanup EXIT
KEYCHAIN_PASSWORD="$(openssl rand -hex 24)"
security create-keychain -p "$KEYCHAIN_PASSWORD" "$KEYCHAIN"
security unlock-keychain -p "$KEYCHAIN_PASSWORD" "$KEYCHAIN"
if ! security import "$P12" -k "$KEYCHAIN" -P "$P12_PASSWORD" -T /usr/bin/codesign >/dev/null; then
  echo "Could not open $P12 with that password." >&2
  exit 1
fi
IDENTITIES="$(security find-identity -p codesigning "$KEYCHAIN" | sed -nE 's/^ *[0-9]+\) [0-9A-F]{40} "(.*)".*$/\1/p' | sort -u)"
COUNT="$(printf '%s\n' "$IDENTITIES" | grep -c . || true)"
if [[ "$COUNT" != "1" ]]; then
  echo "The .p12 must hold exactly one code-signing certificate with its private key (found $COUNT)." >&2
  exit 1
fi
echo "Certificate: $IDENTITIES"
if [[ "$IDENTITIES" != "Developer ID Application:"* ]]; then
  echo "This is not a Developer ID Application certificate. Releases signed with it" >&2
  echo "will still ask for your Mac password after every update." >&2
  read -r -p "Upload it anyway? [y/N] " ANSWER
  [[ "$ANSWER" == [yY] ]] || exit 1
fi

base64 -i "$P12" | gh secret set MACOS_SIGNING_CERT_P12_BASE64 --repo "$REPO"
printf '%s' "$P12_PASSWORD" | gh secret set MACOS_SIGNING_CERT_PASSWORD --repo "$REPO"
echo "Uploaded. The next release from main is signed with this certificate."
echo "After installing that update, click Always Allow once; later updates won't ask."
