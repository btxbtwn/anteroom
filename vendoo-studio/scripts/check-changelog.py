#!/usr/bin/env python3
"""Fail when CHANGELOG.md does not describe the version Studio is shipping.

Pull requests do not bump ``VERSION`` or edit ``CHANGELOG.md``. They add a note
in ``changelog.d/`` and the Version workflow turns it into a version after the
merge (see ``changelog_fragments.py``). Rules:

1. Always: the newest entry is for the current ``VERSION``, has notes, the
   file stays a strictly descending list of unique versions, and every pending
   note in ``changelog.d/`` is well formed.
2. With ``--base <ref>`` (pull requests): ``VERSION`` and ``CHANGELOG.md`` are
   unchanged from the base ref. A pull request that changes ``vendoo-studio/``
   without adding a note gets a warning, since it will not get a version.
"""

from __future__ import annotations

import argparse
import re
import subprocess
import sys
from pathlib import Path

from changelog_fragments import NOTES_DIR, README, read_notes

ROOT = Path(__file__).resolve().parents[2]
CHANGELOG = ROOT / "CHANGELOG.md"
VERSION_FILE = ROOT / "vendoo-studio" / "VERSION"

HEADING = re.compile(
    r"^##\s+(?P<version>\d+\.\d+\.\d+)\s*(?:[—–-]\s*(?P<date>\d{4}-\d{2}-\d{2}))?\s*$"
)


def parse(text: str) -> list[tuple[str, str]]:
    """``[(version, body)]`` in file order. Mirrors services/changelog.py."""
    entries: list[tuple[str, str]] = []
    version: str | None = None
    lines: list[str] = []
    for line in text.splitlines():
        match = HEADING.match(line.strip())
        if match:
            if version is not None:
                entries.append((version, "\n".join(lines).strip()))
            version = match.group("version")
            lines = []
            continue
        if version is not None:
            lines.append(line)
    if version is not None:
        entries.append((version, "\n".join(lines).strip()))
    return entries


def git_show(ref: str, path: str) -> str | None:
    result = subprocess.run(
        ["git", "-C", str(ROOT), "show", f"{ref}:{path}"],
        capture_output=True,
        text=True,
    )
    return result.stdout if result.returncode == 0 else None


def changed_paths(base: str) -> list[str]:
    result = subprocess.run(
        ["git", "-C", str(ROOT), "diff", "--name-status", "--no-renames", base, "HEAD"],
        capture_output=True,
        text=True,
        check=True,
    )
    return [line for line in result.stdout.splitlines() if line]


def check_pull_request(base: str, version: str) -> None:
    base_version = (git_show(base, "vendoo-studio/VERSION") or "").strip()
    if base_version and base_version != version:
        fail(
            f"this pull request changes VERSION ({base_version} → {version}).\n"
            "Versions are bumped after merge. Undo the bump (keep VERSION and the package "
            f"manifests at {base_version}) and add a note in {NOTES_DIR}/ instead."
        )
    if (git_show(base, "CHANGELOG.md") or "") != CHANGELOG.read_text(encoding="utf-8"):
        fail(
            "this pull request edits CHANGELOG.md.\n"
            f"Move your entry into a note in {NOTES_DIR}/ (see {NOTES_DIR}/{README}); "
            "the Version workflow writes CHANGELOG.md after merge."
        )

    changes = [line.split("\t", 1) for line in changed_paths(base)]
    touches_studio = any(path.startswith("vendoo-studio/") for _, path in changes)
    adds_note = any(
        status == "A" and path.startswith(f"{NOTES_DIR}/") and not path.endswith(f"/{README}")
        for status, path in changes
    )
    if touches_studio and not adds_note:
        print(
            f"::warning::This pull request changes vendoo-studio/ but adds no {NOTES_DIR}/ note, "
            "so merging it will not bump the version or add to What's new."
        )


def fail(message: str) -> None:
    raise SystemExit(f"CHANGELOG.md: {message}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base", help="Base ref to compare against (a PR's target branch).")
    args = parser.parse_args()

    version = VERSION_FILE.read_text(encoding="utf-8").strip()
    if args.base:
        check_pull_request(args.base, version)

    entries = parse(CHANGELOG.read_text(encoding="utf-8"))
    if not entries:
        fail("no version entries found. Use '## <version> — <YYYY-MM-DD>' headings.")

    newest, body = entries[0]
    if newest != version:
        fail(
            f"the newest entry is {newest}, but Anteroom is on {version}.\n"
            f"Add a '## {version} — <YYYY-MM-DD>' section at the top describing the change."
        )
    if not body:
        fail(f"the {version} entry has no notes. Say what changed, in the seller's words.")

    versions = [entry[0] for entry in entries]
    duplicates = {item for item in versions if versions.count(item) > 1}
    if duplicates:
        fail(f"duplicate entries for {', '.join(sorted(duplicates))}.")

    parsed = [tuple(int(part) for part in item.split(".")) for item in versions]
    if parsed != sorted(parsed, reverse=True):
        fail("entries must be newest first.")

    try:
        notes = read_notes(ROOT)
    except ValueError as exc:
        fail(f"malformed notes in {NOTES_DIR}/:\n{exc}")

    pending = f", {len(notes)} pending note(s) in {NOTES_DIR}/" if notes else ""
    print(f"CHANGELOG.md documents {version} ({len(entries)} entries){pending}.")


if __name__ == "__main__":
    sys.exit(main())
