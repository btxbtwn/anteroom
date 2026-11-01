#!/usr/bin/env python3
"""Turn pending ``changelog.d/`` notes into versions and ``CHANGELOG.md`` sections.

Run by the Version workflow after a merge to ``main``. Each merged pull
request's notes become one version: ``bump-version.sh`` runs once per pull
request (patch, or minor/major when a note asks for it), its notes become the
new top section of ``CHANGELOG.md``, and the note files are deleted.

Prints each new version. Prints nothing and changes nothing when no notes are
pending.
"""

from __future__ import annotations

import subprocess
import sys
from datetime import UTC, datetime

from changelog_fragments import ROOT, group_by_merge, insert_section, read_notes, strongest

CHANGELOG = ROOT / "CHANGELOG.md"
BUMP = ROOT / "vendoo-studio" / "scripts" / "bump-version.sh"


def bump(kind: str) -> str:
    result = subprocess.run([str(BUMP), kind], capture_output=True, text=True, check=True)
    return result.stdout.strip().splitlines()[-1]


def main() -> int:
    try:
        notes = read_notes()
    except ValueError as exc:
        print(exc, file=sys.stderr)
        return 1
    if not notes:
        return 0

    today = datetime.now(UTC).date().isoformat()
    text = CHANGELOG.read_text(encoding="utf-8")
    for group in group_by_merge(notes):
        version = bump(strongest([note.kind for note in group]))
        body = "\n".join(note.body for note in group)
        text = insert_section(text, version, today, body)
        CHANGELOG.write_text(text, encoding="utf-8")
        for note in group:
            note.path.unlink()
        print(version)
    return 0


if __name__ == "__main__":
    sys.exit(main())
