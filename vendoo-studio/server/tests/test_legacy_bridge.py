"""The bridge release that lets installs signed with the old certificate update.

Builds through 0.1.947 run the same _parse_versioned_tag, _normalize_build_info
and version-picker code as this branch's main (packaged_updates.py blob
5da7782, PreviousVersionsDialog.tsx blob 1f6d5c2), so these check the bridge
against that code. Remove with scripts/legacy_bridge.py after 2027-02-12.
"""

from __future__ import annotations

import hashlib
import importlib.util
import json
import subprocess
import sys
from pathlib import Path

import pytest

from vendoo_studio.services import packaged_updates

SCRIPT = Path(__file__).parents[2] / "scripts" / "legacy_bridge.py"
SPEC = importlib.util.spec_from_file_location("legacy_bridge", SCRIPT)
assert SPEC and SPEC.loader
legacy_bridge = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(legacy_bridge)

BUILD_INFO = {
    "version": "0.1.953",
    "sha": "1793526aaaabbbbccccdddd",
    "short_sha": "1793526",
    "ref": "main",
    "schema_revision": "d8b6f2a19c40",
    "zips": {"Anteroom-macos.zip": "11" * 32, "List-This-Studio-macos.zip": "22" * 32},
    "zip_sha256": "22" * 32,
}


def test_bridge_tag_is_a_version_older_installs_can_pick() -> None:
    tag = legacy_bridge.bridge_tag("studio-macos", "0.1.953", "1793526")
    assert tag == "studio-macos-v0.1.953-oldcert1793526"
    # How 0.1.947 parses it for Settings → About → Previous versions.
    assert packaged_updates._parse_versioned_tag(tag) == ("0.1.953", "oldcert1793526")
    # Never the same tag as the Developer ID build of the same commit.
    assert tag != f"{packaged_updates.VERSIONED_TAG_PREFIX}0.1.953-1793526"
    # Shown with an Install button there: 0.1.947 marks a version installed
    # only when its own sha starts with the tag's suffix.
    assert not "8b284263dfc7a9a082ea6e4eb350bd260a005b78".startswith("oldcert1793526")


def test_bridge_build_info_points_older_installs_at_the_old_certificate_zip(tmp_path: Path) -> None:
    archive = tmp_path / "Anteroom-macos.zip"
    archive.write_bytes(b"signed with the old certificate")
    info = legacy_bridge.bridge_build_info(BUILD_INFO, archive)

    normalized = packaged_updates._normalize_build_info(info)
    assert normalized["zip_sha256"] == hashlib.sha256(archive.read_bytes()).hexdigest()
    # The version picker refuses a build without a layout; a newer layout keeps
    # the database (test_version_rollback covers that rule).
    assert normalized["schema_revision"] == "d8b6f2a19c40"
    assert normalized["version"] == "0.1.953"
    assert "List-This-Studio-macos.zip" not in info["zips"]
    assert "zip_sha256" not in info
    # 0.1.947 downloads this asset name from the selected tag.
    assert packaged_updates.release_asset_url(packaged_updates.ZIP_NAME, "studio-macos-v0.1.953-oldcert1793526").endswith(
        "/releases/download/studio-macos-v0.1.953-oldcert1793526/Anteroom-macos.zip"
    )


def test_bridge_needs_a_schema_revision(tmp_path: Path) -> None:
    archive = tmp_path / "Anteroom-macos.zip"
    archive.write_bytes(b"zip")
    with pytest.raises(SystemExit):
        legacy_bridge.bridge_build_info({**BUILD_INFO, "schema_revision": None}, archive)


def _releases(builds: int, bridges: int) -> list[tuple[str, str]]:
    releases = [("2026-11-01T00:00:00Z", "studio-macos")]
    for number in range(builds):
        releases.append((f"2026-11-{10 + number // 24:02d}T{number % 24:02d}:00:00Z", f"studio-macos-v0.1.{900 + number}-abc{number:04d}"))
    for number in range(bridges):
        releases.append((f"2026-11-0{2 + number}T00:00:00Z", f"studio-macos-v0.1.{800 + number}-oldcert{number:04d}"))
    return releases


def test_pruning_keeps_the_newest_bridge_however_old() -> None:
    releases = _releases(builds=45, bridges=3)
    deleted = legacy_bridge.releases_to_prune("studio-macos", releases, keep=30)
    kept = {tag for _, tag in releases} - set(deleted)
    assert "studio-macos" in kept
    assert "studio-macos-v0.1.802-oldcert0002" in kept
    assert {"studio-macos-v0.1.800-oldcert0000", "studio-macos-v0.1.801-oldcert0001"} <= set(deleted)
    assert sum(1 for tag in kept if not legacy_bridge.is_bridge_tag("studio-macos", tag)) == 31


def test_everything_kept_fits_in_the_first_page_older_installs_read() -> None:
    # 0.1.947 lists only the first 100 releases, in GitHub's order; when no
    # more than 100 exist, the bridge is among them whatever that order is.
    for builds, bridges in [(30, 1), (45, 3), (200, 40)]:
        releases = _releases(builds, bridges)
        deleted = legacy_bridge.releases_to_prune("studio-macos", releases, keep=30)
        assert len(releases) - len(deleted) <= legacy_bridge.LISTED_RELEASES
    with pytest.raises(SystemExit):
        legacy_bridge.releases_to_prune("studio-macos", [], keep=99)


def test_prune_command_reads_the_release_listing() -> None:
    listing = "\n".join(f"{created}\t{tag}" for created, tag in _releases(builds=32, bridges=2))
    result = subprocess.run(
        [sys.executable, str(SCRIPT), "prune", "studio-macos", "30"],
        input=listing, capture_output=True, text=True, check=True,
    )
    assert result.stdout.split() == [
        "studio-macos-v0.1.901-abc0001",
        "studio-macos-v0.1.900-abc0000",
        "studio-macos-v0.1.800-oldcert0000",
    ]


def test_info_command_writes_the_bridge_stamp(tmp_path: Path) -> None:
    source = tmp_path / "build_info.json"
    source.write_text(json.dumps(BUILD_INFO), encoding="utf-8")
    archive = tmp_path / "Anteroom-macos.zip"
    archive.write_bytes(b"zip")
    target = tmp_path / "bridge.json"
    subprocess.run([sys.executable, str(SCRIPT), "info", str(source), str(archive), str(target)], check=True,
                   capture_output=True)
    assert json.loads(target.read_text())["zips"] == {"Anteroom-macos.zip": hashlib.sha256(b"zip").hexdigest()}
