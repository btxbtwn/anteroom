#!/bin/bash
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
REPO="$(cd "$ROOT/.." && pwd)"
ZIP="$ROOT/release/Anteroom-macos.zip"
# Builds from before the rename update from this asset; see package-macos-app.sh.
LEGACY_ZIP="$ROOT/release/List-This-Studio-macos.zip"
CONNECTOR_ZIP="$ROOT/release/Anteroom-Connector-macos.zip"
INFO="$ROOT/release/build_info.json"
# The same build signed with the older self-signed certificate, when the
# release job could sign one. See scripts/legacy_bridge.py.
BRIDGE_ZIP="$ROOT/release/bridge/Anteroom-macos.zip"
BRIDGE_INFO="$ROOT/release/bridge/build_info.json"
TAG="${VENDOO_STUDIO_RELEASE_TAG:-studio-macos}"
DEFAULT_TITLE="Anteroom (macOS)"
GITHUB_REPO="${VENDOO_STUDIO_GITHUB_REPO:-btxbtwn/anteroom}"

for archive in "$ZIP" "$LEGACY_ZIP" "$CONNECTOR_ZIP"; do
  if [[ ! -f "$archive" ]]; then
    echo "Missing $archive. Run scripts/package-macos-app.sh first." >&2
    exit 1
  fi
done
if [[ ! -f "$INFO" ]]; then
  echo "Missing $INFO. Run scripts/package-macos-app.sh first." >&2
  exit 1
fi

SHA="$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1]))["sha"])' "$INFO")"
SHORT="$(python3 -c 'import json,sys; d=json.load(open(sys.argv[1])); print(d.get("short_sha") or d["sha"][:7])' "$INFO")"
REF="$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1])).get("ref") or "main")' "$INFO")"
VERSION="$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1])).get("version") or "")' "$INFO")"
BUILT_AT="$(date -u +'%Y-%m-%d %H:%M UTC')"

# The tag has to end up on this commit, which means GitHub has to have it. Catch
# that here rather than after a 90 MB upload.
if ! gh api "repos/${GITHUB_REPO}/commits/${SHA}" --jq '.sha' >/dev/null 2>&1; then
  echo "Commit $SHORT is not on GitHub. Push $REF before publishing." >&2
  exit 1
fi

# Read the old tag before uploading or moving it. A cancelled build can leave
# several merged PRs between that tag and this SHA, and every one belongs in
# the update message.
RELEASE_EXISTS=0
PREVIOUS_SHA=""
if gh release view "$TAG" --repo "$GITHUB_REPO" >/dev/null 2>&1; then
  RELEASE_EXISTS=1
  PREVIOUS_SHA="$(gh api "repos/${GITHUB_REPO}/git/refs/tags/${TAG}" --jq '.object.sha')"
fi

if [[ -n "$PREVIOUS_SHA" && "$PREVIOUS_SHA" != "$SHA" ]] && \
   ! git -C "$REPO" cat-file -e "${PREVIOUS_SHA}^{commit}" 2>/dev/null; then
  git -C "$REPO" fetch --no-tags origin "$PREVIOUS_SHA"
fi
if [[ -n "$PREVIOUS_SHA" && "$PREVIOUS_SHA" != "$SHA" ]] && \
   ! git -C "$REPO" merge-base --is-ancestor "$PREVIOUS_SHA" "$SHA"; then
  echo "Published build $PREVIOUS_SHA is not an ancestor of $SHORT; refusing incomplete release notes." >&2
  exit 1
fi

PR_ARGS=(
  --repo-path "$REPO"
  --repository "$GITHUB_REPO"
  --head "$SHA"
  --base-ref main
)
if [[ -n "$PREVIOUS_SHA" ]]; then
  PR_ARGS+=(--base "$PREVIOUS_SHA")
fi
PULL_REQUESTS_JSON="$(python3 "$ROOT/scripts/release_pull_requests.py" "${PR_ARGS[@]}")"

# Prefer an explicit override, then the newest included PR, then the commit subject.
TITLE="${VENDOO_STUDIO_RELEASE_TITLE:-}"
if [[ -z "$TITLE" ]]; then
  TITLE="$(python3 -c 'import json,sys; p=json.loads(sys.argv[1]); print(p[0]["title"] if p else "")' "$PULL_REQUESTS_JSON")"
fi
if [[ -z "$TITLE" ]]; then
  TITLE="$(git -C "$REPO" log -1 --format=%s "$SHA" 2>/dev/null || true)"
fi
TITLE="${TITLE:-$DEFAULT_TITLE}"

python3 - "$INFO" "$TITLE" "$PULL_REQUESTS_JSON" <<'PY'
import json
import sys
from pathlib import Path

path = Path(sys.argv[1])
payload = json.loads(path.read_text(encoding="utf-8"))
payload["title"] = sys.argv[2]
payload["pull_requests"] = json.loads(sys.argv[3])
path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
PY

# GitHub stamps a release with the date it was first published and never
# refreshes it, so the page can read months old beside a zip built minutes ago.
# State the build date in the notes, where it is accurate.
CHANGES="$(python3 -c 'import json,sys; print("\n".join("- [#{number}]({url}) {title}".format(**p) for p in json.loads(sys.argv[1])))' "$PULL_REQUESTS_JSON")"
NOTES=$(printf 'version: %s\nbuilt: %s\nsha: %s\nref: %s\n\nIncluded pull requests\n%s\n\nInstall\n1. Unzip Anteroom-macos.zip\n2. Move Anteroom.app into Applications\n3. Open it. It is notarized by Apple, so no Control-click is needed\n4. In Settings, sign in with ChatGPT or add a MiMo API key\n5. Click Connect Chrome, load the listing extension once, and sign in to the marketplaces you sell on\n\nNeeds macOS 13+ and Google Chrome. Listing data stays on this Mac.\n' "${VERSION:-unknown}" "$BUILT_AT" "$SHA" "$REF" "${CHANGES:-- None detected}")

# Keep this build under its own tag as well, so Settings → About can go back to
# it if a later build turns out broken. The rolling tag above is what updates
# follow; these are never marked latest.
VERSIONED_TAG="${TAG}-v${VERSION:-unknown}-${SHORT}"
NOTES+=$'\n\nConnector only\nDownload Anteroom-Connector-macos.zip to use your ChatGPT and Claude subscriptions from the web or iPhone without the full desktop app. Keep the connector running and the Mac awake and online. Claude subscription sign-in requires Claude Code.\n'
if gh release view "$VERSIONED_TAG" --repo "$GITHUB_REPO" >/dev/null 2>&1; then
  gh release upload "$VERSIONED_TAG" "$ZIP" "$CONNECTOR_ZIP" "$INFO" --clobber --repo "$GITHUB_REPO"
else
  gh release create "$VERSIONED_TAG" "$ZIP" "$CONNECTOR_ZIP" "$INFO" \
    --latest=false \
    --title "Anteroom ${VERSION:-unknown} (${SHORT}): ${TITLE}" \
    --notes "$NOTES" \
    --target "$SHA" \
    --repo "$GITHUB_REPO"
fi

if [[ "$RELEASE_EXISTS" == 1 ]]; then
  gh release upload "$TAG" "$ZIP" "$LEGACY_ZIP" "$CONNECTOR_ZIP" "$INFO" --clobber --repo "$GITHUB_REPO"
  gh release edit "$TAG" --title "$TITLE" --notes "$NOTES" --latest --repo "$GITHUB_REPO"
else
  gh release create "$TAG" "$ZIP" "$LEGACY_ZIP" "$CONNECTOR_ZIP" "$INFO" \
    --latest \
    --title "$TITLE" \
    --notes "$NOTES" \
    --target "$SHA" \
    --repo "$GITHUB_REPO"
fi

# --target only applies when the tag is created. Re-publishing clobbers the
# assets and leaves the tag wherever it first landed, so the release page shows
# an unrelated old commit next to a current build. Move the ref itself.
TAG_SHA="$PREVIOUS_SHA"
if [[ "$TAG_SHA" != "$SHA" ]]; then
  PREVIOUS="${TAG_SHA:-unset}"
  if gh api -X PATCH "repos/${GITHUB_REPO}/git/refs/tags/${TAG}" \
    -f "sha=${SHA}" -F force=true --jq '.object.sha' >/dev/null; then
    echo "Moved tag $TAG to $SHORT (was ${PREVIOUS:0:7})"
  else
    echo "Warning: could not move tag $TAG to $SHORT; the release page will show the old commit." >&2
  fi
fi

# Installs signed with the older self-signed certificate (0.1.947 and earlier)
# refuse this Developer ID build. They can pick this bridge, the same build
# signed with their certificate, in Settings → About → Previous versions; its
# updater then accepts the Developer ID builds. Remove after 2027-02-12.
if [[ -f "$BRIDGE_ZIP" && -f "$BRIDGE_INFO" ]]; then
  BRIDGE_TAG="$(python3 "$ROOT/scripts/legacy_bridge.py" tag "$TAG" "${VERSION:-unknown}" "$SHORT")"
  BRIDGE_TITLE="Update for Anteroom 0.1.947 and earlier (installs signed before Developer ID)"
  python3 - "$BRIDGE_INFO" "$BRIDGE_TITLE" <<'PY'
import json
import sys
from pathlib import Path

path = Path(sys.argv[1])
payload = json.loads(path.read_text(encoding="utf-8"))
payload["title"] = sys.argv[2]
payload["pull_requests"] = []
path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
PY
  BRIDGE_NOTES=$(printf 'Anteroom %s (%s), signed with the certificate Anteroom used through 0.1.947.\n\nOnly for a Mac whose Anteroom says an update does not match its signing identity: open Settings → About → Previous versions and install this one. After that, updates install normally. Everyone else: download Anteroom-macos.zip from the latest release.\n' "${VERSION:-unknown}" "$SHORT")
  # Uploaded as Anteroom-macos.zip and build_info.json: the names those
  # installs look for under a version tag.
  BRIDGE_UPLOAD="$(mktemp -d)"
  cp "$BRIDGE_ZIP" "$BRIDGE_UPLOAD/Anteroom-macos.zip"
  cp "$BRIDGE_INFO" "$BRIDGE_UPLOAD/build_info.json"
  if gh release view "$BRIDGE_TAG" --repo "$GITHUB_REPO" >/dev/null 2>&1; then
    gh release upload "$BRIDGE_TAG" "$BRIDGE_UPLOAD/Anteroom-macos.zip" "$BRIDGE_UPLOAD/build_info.json" --clobber --repo "$GITHUB_REPO"
  else
    gh release create "$BRIDGE_TAG" "$BRIDGE_UPLOAD/Anteroom-macos.zip" "$BRIDGE_UPLOAD/build_info.json" \
      --latest=false \
      --title "$BRIDGE_TITLE" \
      --notes "$BRIDGE_NOTES" \
      --target "$SHA" \
      --repo "$GITHUB_REPO"
  fi
  rm -rf "$BRIDGE_UPLOAD"
  echo "Published the bridge for older installs as https://github.com/${GITHUB_REPO}/releases/tag/$BRIDGE_TAG"
fi

# Each build is about 90 MB. Keep the newest few dozen; older ones go with
# their tags. The newest bridge is kept whatever its age, and older bridges go,
# so everything stays inside the first 100 releases the version list reads.
KEEP_VERSIONS="${VENDOO_STUDIO_KEEP_VERSIONS:-30}"
prune_old_versions() {
  gh api --paginate "repos/${GITHUB_REPO}/releases?per_page=100" \
    --jq ".[] | [.created_at, .tag_name] | @tsv" \
    | python3 "$ROOT/scripts/legacy_bridge.py" prune "$TAG" "$KEEP_VERSIONS" \
    | while read -r old_tag; do
        [[ -n "$old_tag" ]] || continue
        gh release delete "$old_tag" --cleanup-tag --yes --repo "$GITHUB_REPO"
        echo "Removed old build $old_tag"
      done
}
# The build is already published; failing to tidy old ones must not fail the job.
prune_old_versions || echo "Warning: could not remove old builds beyond the newest $KEEP_VERSIONS." >&2

echo "Published $SHORT ($TITLE) to https://github.com/${GITHUB_REPO}/releases/tag/$TAG"
echo "Kept this build as https://github.com/${GITHUB_REPO}/releases/tag/$VERSIONED_TAG"
