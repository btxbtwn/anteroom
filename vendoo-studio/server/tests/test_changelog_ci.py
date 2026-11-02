"""Run the real CI changelog step against synthetic GitHub merge commits."""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[3]


def git(root: Path, *args: str) -> str:
    return subprocess.check_output(
        ["git", "-c", "user.name=Test", "-c", "user.email=test@example.invalid", "-C", str(root), *args],
        text=True,
        stderr=subprocess.PIPE,
    ).strip()


def release(root: Path, version: str) -> None:
    (root / "vendoo-studio" / "VERSION").write_text(version + "\n", encoding="utf-8")
    (root / "CHANGELOG.md").write_text(
        f"# Changelog\n\n## {version} — 2026-11-01\n\n- Shipped.\n", encoding="utf-8"
    )


@pytest.fixture
def repository(tmp_path: Path) -> tuple[Path, str]:
    scripts = tmp_path / "vendoo-studio" / "scripts"
    scripts.mkdir(parents=True)
    for name in ("check-changelog.py", "changelog_fragments.py"):
        shutil.copy(ROOT / "vendoo-studio" / "scripts" / name, scripts / name)
    release(tmp_path, "1.0.0")
    git(tmp_path, "init", "-q", "-b", "main")
    git(tmp_path, "add", ".")
    git(tmp_path, "commit", "-qm", "Initial release")
    base = git(tmp_path, "rev-parse", "HEAD")
    git(tmp_path, "checkout", "-qb", "feature")
    notes = tmp_path / "changelog.d"
    notes.mkdir()
    (notes / "feature.md").write_text("- A seller improvement.\n", encoding="utf-8")
    git(tmp_path, "add", ".")
    git(tmp_path, "commit", "-qm", "Feature note")
    git(tmp_path, "checkout", "-q", "main")
    release(tmp_path, "1.0.1")
    git(tmp_path, "add", ".")
    git(tmp_path, "commit", "-qm", "Another PR released while feature was open")
    return tmp_path, base


def run_step(root: Path, event_base: str, event: str = "pull_request") -> subprocess.CompletedProcess[str]:
    workflow = yaml.safe_load((ROOT / ".github" / "workflows" / "ci.yml").read_text())
    step = next(step for step in workflow["jobs"]["changelog"]["steps"] if "run" in step)
    return subprocess.run(
        ["bash", "-e", "-c", step["run"]],
        cwd=root,
        env={
            **os.environ,
            "BASE_SHA": event_base,
            "GITHUB_EVENT_NAME": event,
            "PATH": str(Path(sys.executable).parent) + os.pathsep + os.environ["PATH"],
        },
        capture_output=True,
        text=True,
    )


def test_new_base_release_is_not_a_pr_version_bump(repository: tuple[Path, str]) -> None:
    root, stale_base = repository
    git(root, "merge", "-q", "--no-ff", "feature", "-m", "Synthetic PR merge")
    result = run_step(root, stale_base)
    assert result.returncode == 0, result.stdout + result.stderr
    assert "documents 1.0.1" in result.stdout


@pytest.mark.parametrize("change", ["version", "changelog"])
def test_pr_still_cannot_edit_release_files(repository: tuple[Path, str], change: str) -> None:
    root, stale_base = repository
    git(root, "checkout", "-qb", "bad-feature")
    if change == "version":
        release(root, "1.0.2")
    else:
        with (root / "CHANGELOG.md").open("a") as stream:
            stream.write("\n- Unauthorized release edit.\n")
    git(root, "add", ".")
    git(root, "commit", "-qm", "Edit release files in PR")
    git(root, "checkout", "-q", "main")
    git(root, "merge", "-q", "--no-ff", "bad-feature", "-m", "Synthetic PR merge")
    result = run_step(root, stale_base)
    assert result.returncode != 0
    expected = "changes VERSION (1.0.1 → 1.0.2)" if change == "version" else "edits CHANGELOG.md"
    assert expected in result.stderr


def test_release_push_needs_no_merge_parent(repository: tuple[Path, str]) -> None:
    root, _ = repository
    result = run_step(root, "", "push")
    assert result.returncode == 0, result.stdout + result.stderr
    assert "documents 1.0.1" in result.stdout
