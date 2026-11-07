""".github/scripts/changed-components.py: CI skips only what a change cannot affect."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]
SCRIPT = ROOT / ".github" / "scripts" / "changed-components.py"


def git(root: Path, *args: str) -> str:
    return subprocess.run(["git", *args], cwd=root, check=True, capture_output=True, text=True).stdout.strip()


def components(root: Path, *args: str, stdin: str | None = None) -> dict[str, str]:
    result = subprocess.run(
        [sys.executable, str(SCRIPT), *args], cwd=root, check=True, capture_output=True, text=True, input=stdin
    )
    return dict(line.split("=", 1) for line in result.stdout.splitlines())


def write_release(root: Path, version: str) -> None:
    studio = root / "vendoo-studio"
    (studio / "VERSION").write_text(f"{version}\n", encoding="utf-8")
    package = {"name": "vendoo-studio", "version": version, "dependencies": {"react": "^19"}}
    (studio / "package.json").write_text(json.dumps(package, indent=2) + "\n", encoding="utf-8")
    lock = {"version": version, "packages": {"": {"version": version}}}
    (studio / "package-lock.json").write_text(json.dumps(lock, indent=2) + "\n", encoding="utf-8")
    (studio / "pyproject.toml").write_text(
        f'[project]\nname = "vendoo-studio"\nversion = "{version}"\ndependencies = ["fastapi>=0.121"]\n',
        encoding="utf-8",
    )
    (studio / "uv.lock").write_text(
        f'[[package]]\nname = "fastapi"\nversion = "0.121.0"\n\n[[package]]\nname = "vendoo-studio"\nversion = "{version}"\n',
        encoding="utf-8",
    )
    changelog = root / "CHANGELOG.md"
    previous = changelog.read_text(encoding="utf-8") if changelog.exists() else ""
    changelog.write_text(f"## {version}\n\n- A change.\n\n{previous}", encoding="utf-8")


@pytest.fixture
def repository(tmp_path: Path) -> tuple[Path, str]:
    git(tmp_path, "init", "-q", "-b", "main")
    git(tmp_path, "config", "user.email", "test@example.com")
    git(tmp_path, "config", "user.name", "Test")
    (tmp_path / "vendoo-studio").mkdir()
    write_release(tmp_path, "1.0.0")
    (tmp_path / "changelog.d").mkdir()
    (tmp_path / "changelog.d" / "feature.md").write_text("- A change.\n", encoding="utf-8")
    git(tmp_path, "add", ".")
    git(tmp_path, "commit", "-qm", "Merge a pull request")
    return tmp_path, git(tmp_path, "rev-parse", "HEAD")


def test_a_release_commit_skips_every_test_job(repository: tuple[Path, str]) -> None:
    root, base = repository
    write_release(root, "1.0.1")
    (root / "changelog.d" / "feature.md").unlink()
    git(root, "add", "-A")
    git(root, "commit", "-qm", "Release Anteroom 1.0.1")
    assert components(root, "--base", base) == {"studio": "false", "extension": "false", "relay": "false"}


@pytest.mark.parametrize(
    ("path", "expected"),
    [
        ("vendoo-studio/package.json", {"studio": "true", "extension": "true", "relay": "false"}),
        ("vendoo-studio/uv.lock", {"studio": "true", "extension": "false", "relay": "false"}),
    ],
)
def test_a_dependency_change_beside_the_version_still_runs(
    repository: tuple[Path, str], path: str, expected: dict[str, str]
) -> None:
    root, base = repository
    write_release(root, "1.0.1")
    target = root / path
    target.write_text(target.read_text(encoding="utf-8").replace("^19", "^20").replace("0.121.0", "0.122.0"), encoding="utf-8")
    git(root, "add", "-A")
    git(root, "commit", "-qm", "Release with a dependency bump")
    assert components(root, "--base", base) == expected


def test_without_a_known_base_everything_runs(repository: tuple[Path, str]) -> None:
    root, _ = repository
    everything = {"studio": "true", "extension": "true", "relay": "true"}
    assert components(root, "--base", "") == everything
    assert components(root, "--base", "0" * 40) == everything
    assert components(root, "--base", "f" * 40) == everything


@pytest.mark.parametrize(
    ("paths", "expected"),
    [
        ("docs/hosted-production.md\nREADME.md\n", {"studio": "false", "extension": "false", "relay": "false"}),
        ("webhook-relay/src/index.js\n", {"studio": "false", "extension": "false", "relay": "true"}),
        ("anteroom-ios/project.yml\n", {"studio": "false", "extension": "false", "relay": "false"}),
        # Backend tests read the extension and skills.
        ("vendoo-extension/background.js\n", {"studio": "true", "extension": "true", "relay": "false"}),
        ("skills/list-this/SKILL.md\n", {"studio": "true", "extension": "false", "relay": "false"}),
        (".github/workflows/ci.yml\n", {"studio": "true", "extension": "true", "relay": "true"}),
    ],
)
def test_paths_choose_components(tmp_path: Path, paths: str, expected: dict[str, str]) -> None:
    assert components(tmp_path, "--stdin", stdin=paths) == expected
