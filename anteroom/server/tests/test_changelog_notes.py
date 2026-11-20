"""changelog.d/ notes: validation, merge grouping, and CHANGELOG.md insertion."""

from __future__ import annotations

import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parents[2] / "scripts"
sys.path.insert(0, str(SCRIPTS))

from changelog_fragments import (  # noqa: E402
    group_by_merge,
    insert_section,
    read_note,
    read_notes,
    strongest,
)


def git(root: Path, *args: str) -> None:
    subprocess.run(
        ["git", "-c", "user.name=t", "-c", "user.email=t@t", "-C", str(root), *args],
        check=True,
        capture_output=True,
    )


class NoteTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        (self.root / "changelog.d").mkdir()

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def write(self, name: str, body: str) -> Path:
        path = self.root / "changelog.d" / name
        path.write_text(body, encoding="utf-8")
        return path

    def test_kind_comes_from_the_name(self) -> None:
        self.assertEqual(read_note(self.write("fix-x.md", "- Fixed.\n")).kind, "patch")
        self.assertEqual(read_note(self.write("feat-y.minor.md", "- Added.\n")).kind, "minor")
        self.assertEqual(read_note(self.write("big-z.major.md", "- Changed.\n")).kind, "major")

    def test_rejects_bad_names_and_bodies(self) -> None:
        for name, body in [
            ("Bad_Name.md", "- ok\n"),
            ("fix.patch.md", "- ok\n"),
            ("fix.txt", "- ok\n"),
            ("empty.md", "\n"),
            ("prose.md", "Fixed a thing.\n"),
            ("heading.md", "- ok\n## 1.2.3\n"),
        ]:
            with self.subTest(name=name), self.assertRaises(ValueError):
                read_note(self.write(name, body))

    def test_allows_indented_continuation_lines(self) -> None:
        note = read_note(self.write("long.md", "- First line\n  continues here.\n- Second.\n"))
        self.assertEqual(note.body, "- First line\n  continues here.\n- Second.")

    def test_readme_is_not_a_note(self) -> None:
        self.write("README.md", "# How to write notes\n")
        self.assertEqual(read_notes(self.root), [])

    def test_strongest_kind_wins(self) -> None:
        self.assertEqual(strongest(["patch", "minor", "patch"]), "minor")
        self.assertEqual(strongest(["patch"]), "patch")

    def test_groups_by_merge_commit_oldest_first(self) -> None:
        git(self.root, "init", "-q", "-b", "main")
        git(self.root, "commit", "-q", "--allow-empty", "-m", "base")
        for branch, names in [("a", ["a.md"]), ("b", ["b.md", "b-extra.md"])]:
            git(self.root, "checkout", "-q", "-b", branch, "main")
            for name in names:
                self.write(name, f"- {name}\n")
            git(self.root, "add", "-A")
            git(self.root, "commit", "-q", "-m", branch)
            git(self.root, "checkout", "-q", "main")
            git(self.root, "merge", "-q", "--no-ff", branch, "-m", f"merge {branch}")
        self.write("local.md", "- not committed\n")

        groups = group_by_merge(read_notes(self.root), self.root)

        self.assertEqual(
            [sorted(note.path.name for note in group) for group in groups],
            [["a.md"], ["b-extra.md", "b.md"], ["local.md"]],
        )


class InsertSectionTest(unittest.TestCase):
    def test_goes_above_the_newest_entry_below_the_intro(self) -> None:
        text = "# Changelog\n\nIntro.\n\n## 0.1.1 — 2026-01-01\n\n- Old.\n"
        result = insert_section(text, "0.1.2", "2026-02-02", "- New.")
        self.assertEqual(
            result,
            "# Changelog\n\nIntro.\n\n## 0.1.2 — 2026-02-02\n\n- New.\n\n## 0.1.1 — 2026-01-01\n\n- Old.\n",
        )

    def test_appends_when_there_are_no_entries(self) -> None:
        result = insert_section("# Changelog\n", "0.1.0", "2026-01-01", "- First.")
        self.assertEqual(result, "# Changelog\n\n## 0.1.0 — 2026-01-01\n\n- First.\n\n")


if __name__ == "__main__":
    unittest.main()
