#!/usr/bin/env python3
"""The bridge release for installs signed with the older self-signed certificate.

Anteroom through 0.1.947 was signed with a self-signed certificate, and its
updater only installs a build signed with that same certificate. Releases are
now signed with Developer ID, so those installs refuse every new build on the
rolling release. Until 2027-02-12, a release that can also be signed with the
old certificate publishes that copy under its own version tag,
"studio-macos-v<version>-oldcert<sha>". Those installs pick it in Settings →
About → Previous versions (which accepts a newer build, and keeps the
database since the newer build migrates it forward); the bridge's updater then
accepts the Developer ID builds on the rolling release.

    legacy_bridge.py tag <release tag> <version> <short sha>
    legacy_bridge.py info <build_info.json> <bridge zip> <bridge build_info.json>
    legacy_bridge.py prune <release tag> <keep>   (reads "created_at<TAB>tag" lines)

Remove this script, its callers and its tests after 2027-02-12.
"""

from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

SUFFIX_PREFIX = "oldcert"
# The asset name every build through 0.1.947 downloads from a version tag.
ZIP_NAME = "Anteroom-macos.zip"
# Builds through 0.1.947 list only the first page of 100 GitHub releases when
# offering versions; everything published has to fit in it.
LISTED_RELEASES = 100


def bridge_tag(release_tag: str, version: str, short_sha: str) -> str:
    # The suffix has no hyphen: the version picker splits the tag at the last one.
    return f"{release_tag}-v{version}-{SUFFIX_PREFIX}{short_sha}"


def is_bridge_tag(release_tag: str, tag: str) -> bool:
    return tag.startswith(f"{release_tag}-v") and tag.rpartition("-")[2].startswith(SUFFIX_PREFIX)


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def bridge_build_info(build_info: dict, archive: Path) -> dict:
    """The build stamp for the bridge release: the same build, the bridge zip's digest.

    The version picker refuses a build without a schema revision, so it is required.
    """
    if not build_info.get("schema_revision"):
        raise SystemExit("build_info.json has no schema_revision; the version picker would refuse the bridge.")
    info = {key: value for key, value in build_info.items() if key not in {"zips", "zip_sha256"}}
    info["zips"] = {ZIP_NAME: sha256(archive)}
    return info


def releases_to_prune(release_tag: str, releases: list[tuple[str, str]], keep: int) -> list[str]:
    """Version tags to delete: all but the newest ``keep`` builds and the newest bridge.

    The bridge is kept whatever its age, and at most one is kept, so every
    published release stays inside the first page older builds list.
    """
    if keep + 2 > LISTED_RELEASES:
        raise SystemExit(f"Keeping {keep} builds leaves no room for the bridge in the first {LISTED_RELEASES} releases.")
    versioned = sorted(
        ((created, tag) for created, tag in releases if tag.startswith(f"{release_tag}-v")),
        reverse=True,
    )
    builds = [tag for _, tag in versioned if not is_bridge_tag(release_tag, tag)]
    bridges = [tag for _, tag in versioned if is_bridge_tag(release_tag, tag)]
    return builds[keep:] + bridges[1:]


def main(argv: list[str]) -> None:
    command, *args = argv or [""]
    if command == "tag" and len(args) == 3:
        print(bridge_tag(*args))
    elif command == "info" and len(args) == 3:
        source, archive, target = map(Path, args)
        info = bridge_build_info(json.loads(source.read_text(encoding="utf-8")), archive)
        target.write_text(json.dumps(info, indent=2) + "\n", encoding="utf-8")
        print(f"Bridge build_info {ZIP_NAME} sha256={info['zips'][ZIP_NAME]}")
    elif command == "prune" and len(args) == 2:
        releases = [(created, tag) for created, _, tag in
                    (line.partition("\t") for line in sys.stdin.read().splitlines()) if tag]
        print("\n".join(releases_to_prune(args[0], releases, int(args[1]))))
    else:
        raise SystemExit(__doc__)


if __name__ == "__main__":
    main(sys.argv[1:])
