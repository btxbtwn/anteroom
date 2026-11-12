#!/bin/bash
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
REPO="$(cd "$ROOT/.." && pwd)"
PYTHON="${PYTHON:-$ROOT/.venv/bin/python}"
RELEASE="$ROOT/release"
APP="$RELEASE/Anteroom.app"
ZIP="$RELEASE/Anteroom-macos.zip"
# Builds from before the rename download this zip and look for this bundle in
# it. Publishing the same app under the old names lets them update once; after
# that the updater moves the app to Anteroom.app. Drop both once no install
# older than the rename is left.
LEGACY_ZIP="$RELEASE/List-This-Studio-macos.zip"
LEGACY_APP_NAME="List This Studio.app"
# Installs signed with the older self-signed certificate (through 0.1.947)
# refuse any build not signed with it. Until 2027-02-12, when that certificate
# is available (MACOS_LEGACY_SIGNING_IDENTITY), the same build is also signed
# with it into release/bridge/, which publish-macos-release.sh publishes as a
# separate version those installs pick in Settings → About → Previous
# versions. See scripts/legacy_bridge.py; remove all of it after that date.
LEGACY_SIGN_IDENTITY="${MACOS_LEGACY_SIGNING_IDENTITY:-}"
BRIDGE="$RELEASE/bridge"
LEGACY_SIGNED_APP="$BRIDGE/payload/Anteroom.app"

SIGN_IDENTITY="${MACOS_SIGNING_IDENTITY:-}"
if [[ -z "$SIGN_IDENTITY" || "$SIGN_IDENTITY" == "-" ]]; then
  echo "MACOS_SIGNING_IDENTITY must name a persistent code-signing certificate." >&2
  echo "Ad-hoc signing changes the app's Keychain identity on every update and loses saved sign-ins." >&2
  exit 1
fi

if [[ ! -x "$PYTHON" ]]; then
  echo "Create the Python environment first:" >&2
  echo "  cd $ROOT && python3.12 -m venv .venv" >&2
  exit 1
fi

"$PYTHON" -c 'import sys; raise SystemExit(0 if sys.version_info >= (3, 12) else 1)' || {
  echo "Packaging requires Python 3.12 or newer." >&2
  exit 1
}

cd "$ROOT"
# The exact packages uv.lock pins, hash-checked, so the app ships what CI tested.
"$ROOT/scripts/install-locked.sh" "$PYTHON" package
if [[ ! -d node_modules ]]; then
  npm install
fi
npm run build
if [[ ! -f dist/index.html ]]; then
  echo "Frontend build did not produce dist/index.html." >&2
  exit 1
fi
# The bundle and the VERSION the backend reports have to be the same build, or
# the shipped app shows a version number the UI does not correspond to.
"$PYTHON" - "$ROOT" <<'PY'
import json
import sys
from pathlib import Path

root = Path(sys.argv[1])
stamp_path = root / "dist" / "build-stamp.json"
if not stamp_path.is_file():
    raise SystemExit(f"Frontend build did not produce {stamp_path}.")
stamp = json.loads(stamp_path.read_text(encoding="utf-8"))
version = (root / "VERSION").read_text(encoding="utf-8").strip()
if stamp.get("version") != version:
    raise SystemExit(
        f"Bundle was built from {stamp.get('version')!r} but VERSION is {version!r}. "
        "Re-run npm run build."
    )
print(f"Bundle stamp {version} ({stamp.get('short_sha') or 'no sha'})")
PY

SHA="$(git -C "$REPO" rev-parse HEAD)"
SHORT="$(git -C "$REPO" rev-parse --short HEAD)"
REF="$(git -C "$REPO" rev-parse --abbrev-ref HEAD)"
VERSION="$(tr -d '[:space:]' < "$ROOT/VERSION")"
"$PYTHON" - "$ROOT/desktop/build_info.json" "$SHA" "$SHORT" "$REF" "$VERSION" "$ROOT/server/vendoo_studio/migrations" <<'PY'
import json
import sys
from pathlib import Path

from alembic.config import Config
from alembic.script import ScriptDirectory

# The database layout this build expects. Installing an earlier build reads it
# to tell whether that build can open the current database.
config = Config()
config.set_main_option("script_location", sys.argv[6])
schema_revision = ScriptDirectory.from_config(config).get_current_head()
if not schema_revision:
    raise SystemExit(f"No migration head found in {sys.argv[6]}")

path = Path(sys.argv[1])
path.write_text(json.dumps({
    "version": sys.argv[5],
    "sha": sys.argv[2],
    "short_sha": sys.argv[3],
    "ref": sys.argv[4],
    "schema_revision": schema_revision,
}, indent=2) + "\n", encoding="utf-8")
print(path)
PY

"$PYTHON" "$ROOT/desktop/make_icon.py" "$ROOT/desktop/AppIcon.icns"
rm -rf "$RELEASE" "$ROOT/build/pyinstaller"
mkdir -p "$RELEASE"
"$PYTHON" -m PyInstaller \
  --noconfirm \
  --distpath "$RELEASE" \
  --workpath "$ROOT/build/pyinstaller" \
  "$ROOT/desktop/Anteroom.spec"

if [[ ! -d "$APP" ]]; then
  echo "PyInstaller did not create $APP" >&2
  exit 1
fi

# macOS only draws the current (larger) traffic lights for binaries linked
# against the macOS 26 SDK. PyInstaller's bootloader reports a much older SDK;
# stamp the executable before signing so the shipped app matches T3 Code.
EXE="$APP/Contents/MacOS/Anteroom"
if command -v vtool >/dev/null 2>&1 && [[ -f "$EXE" ]]; then
  MINOS="$(vtool -show-build "$EXE" 2>/dev/null | awk '/minos/{print $2; exit}')"
  MINOS="${MINOS:-13.0}"
  echo "Stamping $EXE with macOS SDK 26.0 (minos $MINOS)"
  vtool -set-build-version macos "$MINOS" 26.0 -replace -output "$EXE" "$EXE"
fi

# Keep PyInstaller framework symlinks. Flattening them breaks the .app layout.
# Every binary inside, Python.framework included, is re-signed with one
# identity, or dyld refuses to load code with a different Team ID. Preserve
# distribution metadata: HTTP clients used by catalog/photo analysis read it
# when they are imported.
if ! command -v codesign >/dev/null 2>&1; then
  echo "codesign is required to package Anteroom.app" >&2
  exit 1
fi
"$PYTHON" - "$APP" <<'PY'
import importlib.metadata
import sys
from pathlib import Path

runtime = Path(sys.argv[1]) / "Contents" / "Frameworks"
metadata = {dist.metadata["Name"].lower(): dist.version
            for dist in importlib.metadata.distributions(path=[str(runtime)])}
for name in ("httpx2", "httpcore2"):
    expected = importlib.metadata.version(name)
    if metadata.get(name) != expected:
        raise SystemExit(f"Bundled {name} metadata is missing or differs from {expected}")
print("Bundled HTTP client runtime metadata verified")
PY
if [[ -n "$LEGACY_SIGN_IDENTITY" ]]; then
  rm -rf "$BRIDGE"
  mkdir -p "$(dirname "$LEGACY_SIGNED_APP")"
  ditto "$APP" "$LEGACY_SIGNED_APP"
fi

check_signature() {
  local app="$1" detail requirement
  detail="$(codesign -dv "$app" 2>&1)" || {
    echo "$detail" >&2
    exit 1
  }
  echo "$detail"
  if [[ "$detail" == *"not signed at all"* ]]; then
    echo "Packaged app is unsigned." >&2
    exit 1
  fi
  if [[ "$detail" != *"(runtime)"* ]]; then
    echo "$app is not signed with the hardened runtime, which notarization requires." >&2
    exit 1
  fi
  # The updater refuses any download that fails this same check.
  codesign --verify --deep --strict --verbose=1 "$app"
  requirement="$(codesign -d -r- "$app" 2>&1)"
  echo "$requirement"
  if [[ "$detail" == *"Signature=adhoc"* ]] || \
     [[ "$requirement" != *"certificate leaf"* && "$requirement" != *"anchor "* ]]; then
    echo "Signed app has no certificate-based designated requirement." >&2
    exit 1
  fi
}

# Inside-out, with the hardened runtime and a secure timestamp; see the script.
"$PYTHON" "$ROOT/scripts/sign_macos_bundle.py" "$APP" "$SIGN_IDENTITY"
check_signature "$APP"
CODESIGN_DV="$(codesign -dv "$APP" 2>&1)"
# Keychain only trusts later builds by their Apple Team ID. A certificate that
# Apple did not issue has none, so macOS ties saved sign-ins to this exact
# build and asks for the Mac password once after every update.
TEAM_ID="$(printf '%s\n' "$CODESIGN_DV" | sed -n 's/^TeamIdentifier=//p' | head -1)"
if [[ "$TEAM_ID" == "not set" ]]; then
  TEAM_ID=""
fi
if [[ -z "$TEAM_ID" ]]; then
  MESSAGE="Signed without an Apple Team ID: macOS will ask for the Keychain password once after this update. Run scripts/setup-signing.sh with a Developer ID Application certificate to stop that."
  if [[ -n "${GITHUB_ACTIONS:-}" ]]; then
    echo "::warning title=Keychain prompt on update::$MESSAGE"
  else
    echo "Warning: $MESSAGE" >&2
  fi
else
  echo "Signed with Apple Team ID $TEAM_ID: updates keep Keychain access."
fi
# Apple checks the app and staples a ticket to it, so Gatekeeper opens a fresh
# download without the Control-click workaround. Skipped (with a log line)
# without an App Store Connect key; release builds require one.
"$ROOT/scripts/notarize-macos-app.sh" "$APP"

if [[ -n "$LEGACY_SIGN_IDENTITY" ]]; then
  echo "Signing the bridge copy with the older self-signed certificate"
  "$PYTHON" "$ROOT/scripts/sign_macos_bundle.py" "$LEGACY_SIGNED_APP" "$LEGACY_SIGN_IDENTITY"
  check_signature "$LEGACY_SIGNED_APP"
elif [[ -n "$TEAM_ID" ]]; then
  MESSAGE="MACOS_LEGACY_SIGNING_CERT_P12_BASE64 is not set, so no bridge release is built. Installs signed with the older self-signed certificate (0.1.947 and earlier) cannot update in the app and have to download Anteroom-macos.zip once by hand."
  if [[ -n "${GITHUB_ACTIONS:-}" ]]; then
    echo "::warning title=No bridge for older installs::$MESSAGE"
  else
    echo "Warning: $MESSAGE" >&2
  fi
fi

PAYLOAD="$RELEASE/payload"
LEGACY_PAYLOAD="$RELEASE/legacy-payload"
rm -rf "$PAYLOAD" "$LEGACY_PAYLOAD" "$ZIP" "$LEGACY_ZIP"
mkdir -p "$PAYLOAD" "$LEGACY_PAYLOAD"
ditto "$APP" "$PAYLOAD/Anteroom.app"
cp "$ROOT/desktop/HowToOpen.txt" "$PAYLOAD/How to Open.txt"
(
  cd "$PAYLOAD"
  ditto -c -k . "$ZIP"
)
# The bundle's directory name is not part of its code signature, so the
# renamed copy is the same signed app.
ditto "$APP" "$LEGACY_PAYLOAD/$LEGACY_APP_NAME"
(
  cd "$LEGACY_PAYLOAD"
  ditto -c -k . "$LEGACY_ZIP"
)
ARCHIVES=("$ZIP" "$LEGACY_ZIP")
if [[ -n "$LEGACY_SIGN_IDENTITY" ]]; then
  (
    cd "$BRIDGE/payload"
    ditto -c -k . "$BRIDGE/Anteroom-macos.zip"
  )
  ARCHIVES+=("$BRIDGE/Anteroom-macos.zip")
fi
for archive in "${ARCHIVES[@]}"; do
"$PYTHON" - "$archive" <<'PY'
import sys
import tempfile
from pathlib import Path

from vendoo_studio.services.bundle_symlinks import zip_symlink_members
from vendoo_studio.services.packaged_updates import PackagedUpdateError, _validate_zip_members

archive = Path(sys.argv[1])
links = zip_symlink_members(archive)
print(f"Update zip has {len(links)} symbolic link member(s)")
try:
    with tempfile.TemporaryDirectory() as tmp:
        _validate_zip_members(archive, Path(tmp))
except PackagedUpdateError as exc:
    raise SystemExit(f"Update zip has unsafe symbolic links: {exc}") from exc
print("Update zip symlinks are safe in-bundle relative links")
PY
done
cp "$ROOT/desktop/build_info.json" "$RELEASE/build_info.json"
"$PYTHON" - "$RELEASE/build_info.json" "$ZIP" "$LEGACY_ZIP" <<'PY'
import hashlib
import json
import sys
from pathlib import Path


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


info_path = Path(sys.argv[1])
current, legacy = Path(sys.argv[2]), Path(sys.argv[3])
payload = json.loads(info_path.read_text(encoding="utf-8"))
payload["zips"] = {current.name: sha256(current), legacy.name: sha256(legacy)}
# Builds from before the rename read only this key, and download the legacy zip.
payload["zip_sha256"] = payload["zips"][legacy.name]
info_path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
for name, digest in payload["zips"].items():
    print(f"Release build_info {name} sha256={digest}")
PY
if [[ -n "$LEGACY_SIGN_IDENTITY" ]]; then
  "$PYTHON" "$ROOT/scripts/legacy_bridge.py" info "$RELEASE/build_info.json" \
    "$BRIDGE/Anteroom-macos.zip" "$BRIDGE/build_info.json"
fi
echo "Built $APP"
echo "Share $ZIP"
echo "Publish with: $ROOT/scripts/publish-macos-release.sh"
echo "Recipients: unzip, move Anteroom.app to Applications and open it; How to Open.txt has the rest."
echo "They need macOS 13+, Google Chrome, and a ChatGPT account or Xiaomi MiMo API key. Python is included; the frontend is prebuilt."
