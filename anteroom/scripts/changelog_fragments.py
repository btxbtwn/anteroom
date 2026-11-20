"""Pending changelog notes in ``changelog.d/``.

A pull request never edits ``CHANGELOG.md`` or ``VERSION``. It adds one note
file instead, and the Version workflow turns every note that reaches ``main``
into a version bump and a ``CHANGELOG.md`` section. Files with unique names
cannot conflict, so open pull requests no longer race for version numbers.

A note is ``changelog.d/<slug>.md`` (patch bump), ``<slug>.minor.md`` or
``<slug>.major.md``, holding one or more ``- `` bullets in the seller's words.
"""

from __future__ import annotations

import re
import subprocess
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
NOTES_DIR = "changelog.d"
README = "README.md"
KINDS = ("patch", "minor", "major")

NAME = re.compile(r"^[a-z0-9][a-z0-9-]*(?:\.(?P<kind>minor|major))?\.md$")
HEADING = re.compile(r"^##\s+\d+\.\d+\.\d+")


@dataclass(frozen=True)
class Note:
    path: Path
    kind: str
    body: str


def note_paths(root: Path = ROOT) -> list[Path]:
    folder = root / NOTES_DIR
    if not folder.is_dir():
        return []
    return sorted(p for p in folder.iterdir() if p.is_file() and p.name != README)


def read_note(path: Path) -> Note:
    """Parse one note, raising ``ValueError`` with a fix-it message when it is malformed."""
    match = NAME.match(path.name)
    if not match:
        raise ValueError(
            f"{NOTES_DIR}/{path.name}: name it '<slug>.md', '<slug>.minor.md' or '<slug>.major.md' "
            "using lowercase letters, digits and hyphens."
        )
    body = path.read_text(encoding="utf-8").strip()
    lines = body.splitlines()
    if not lines or not lines[0].startswith("- "):
        raise ValueError(f"{NOTES_DIR}/{path.name}: start with a '- ' bullet that says what changed for sellers.")
    for line in lines:
        if line.strip() and not (line.startswith("- ") or line.startswith("  ")):
            raise ValueError(
                f"{NOTES_DIR}/{path.name}: every line must be a '- ' bullet or an indented continuation."
            )
        if HEADING.match(line.strip()):
            raise ValueError(f"{NOTES_DIR}/{path.name}: do not add version headings; the Version workflow does.")
    return Note(path=path, kind=match.group("kind") or "patch", body=body)


def read_notes(root: Path = ROOT) -> list[Note]:
    """Every pending note, or ``ValueError`` listing all malformed ones at once."""
    notes: list[Note] = []
    errors: list[str] = []
    for path in note_paths(root):
        try:
            notes.append(read_note(path))
        except ValueError as exc:
            errors.append(str(exc))
    if errors:
        raise ValueError("\n".join(errors))
    return notes


def group_by_merge(notes: list[Note], root: Path = ROOT) -> list[list[Note]]:
    """Group notes by the ``main`` commit that brought them in, oldest first.

    One merged pull request becomes one version, even if it added two notes.
    Notes git does not know about yet (a local run) form a final group.
    """
    log = subprocess.run(
        ["git", "-C", str(root), "log", "--first-parent", "--diff-filter=A", "--format=%x00%H", "--name-only", "--", NOTES_DIR],
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    added_in: dict[str, str] = {}
    order: list[str] = []
    for block in log.split("\0")[1:]:
        sha, *names = [line for line in block.splitlines() if line]
        order.append(sha)
        for name in names:
            # The log runs newest first; keep the oldest commit that added the path.
            added_in[name] = sha
    order.reverse()

    groups: dict[str, list[Note]] = {}
    for note in notes:
        key = added_in.get(f"{NOTES_DIR}/{note.path.name}", "")
        groups.setdefault(key, []).append(note)
    ranked = sorted(groups, key=lambda sha: order.index(sha) if sha in order else len(order))
    return [groups[sha] for sha in ranked]


def strongest(kinds: list[str]) -> str:
    return max(kinds, key=KINDS.index)


def insert_section(changelog: str, version: str, date: str, body: str) -> str:
    """Put a ``## <version> — <date>`` section above the newest existing entry."""
    section = f"## {version} — {date}\n\n{body.strip()}\n\n"
    lines = changelog.splitlines(keepends=True)
    for index, line in enumerate(lines):
        if HEADING.match(line.strip()):
            return "".join(lines[:index]) + section + "".join(lines[index:])
    return changelog.rstrip("\n") + "\n\n" + section
