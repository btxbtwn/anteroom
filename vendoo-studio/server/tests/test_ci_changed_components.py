""".github/scripts/changed-components.py: CI skips only what a change cannot affect."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]
SCRIPT = ROOT / ".github" / "scripts" / "changed-components.py"
GROUPS = ("frontend", "backend", "browser", "health", "audit", "extension", "relay")


def flags(*enabled: str) -> dict[str, str]:
    return {name: "true" if name in enabled else "false" for name in GROUPS}


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
    assert components(root, "--base", base) == flags()


@pytest.mark.parametrize(
    ("path", "expected"),
    [
        ("vendoo-studio/package.json", flags("frontend", "backend", "browser", "health", "extension")),
        ("vendoo-studio/uv.lock", flags("backend", "browser", "health", "audit")),
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
    everything = flags(*GROUPS)
    assert components(root, "--base", "") == everything
    assert components(root, "--base", "0" * 40) == everything
    assert components(root, "--base", "f" * 40) == everything


@pytest.mark.parametrize(
    ("paths", "expected"),
    [
        ("docs/hosted-production.md\nREADME.md\n", flags()),
        ("webhook-relay/src/index.js\n", flags("relay")),
        ("anteroom-ios/project.yml\n", flags()),
        # The shared API contract and the Swift generated from it are checked by Studio's suite.
        ("contracts/anteroom-api.json\n", flags("backend", "health")),
        ("anteroom-ios/Sources/API/Generated/APIContract.swift\n", flags("backend", "health")),
        # Backend tests read the extension and skills.
        ("vendoo-extension/background.js\n", flags("backend", "browser", "health", "extension")),
        ("skills/list-this/SKILL.md\n", flags("backend", "browser", "health")),
        # The editor imports the field list; the extension's value matrix reads the option lists.
        ("skills/list-this/references/listing-editor-fields.json\n",
         flags("frontend", "backend", "browser", "health", "extension")),
        # The web app shows the extension's version from its manifest.
        ("vendoo-extension/manifest.json\n", flags("frontend", "backend", "browser", "health", "extension")),
        # test_xcodegen_installer.py reads the installer and the iPhone workflow.
        ("anteroom-ios/tools/install-xcodegen.sh\n", flags("backend", "health")),
        ("anteroom-ios/ci_scripts/ci_post_clone.sh\n", flags("backend", "health")),
        (".github/workflows/ci.yml\n", flags(*GROUPS)),
        (".github/workflows/dependency-audit.yml\n", flags(*GROUPS)),
        ("check.sh\n", flags(*GROUPS)),
        ("vendoo-studio/src/App.tsx\n", flags("frontend", "backend", "browser", "health")),
        ("vendoo-studio/src/App.test.tsx\n", flags("frontend", "backend", "health")),
        ("vendoo-studio/e2e/editor.e2e.ts\n", flags("frontend", "backend", "browser", "health")),
        ("vendoo-studio/vite.config.ts\n", flags("frontend", "backend", "browser", "health")),
        ("unknown-input.json\n", flags("frontend", "backend", "browser", "health")),
        ("vendoo-studio/pyproject.toml\n", flags("backend", "browser", "health", "audit")),
        ("vendoo-studio/scripts/install-locked.sh\n", flags("backend", "browser", "health", "audit")),
    ],
)
def test_paths_choose_components(tmp_path: Path, paths: str, expected: dict[str, str]) -> None:
    assert components(tmp_path, "--stdin", stdin=paths) == expected


def test_backend_fix_does_not_build_frontend_or_audit_dependencies(tmp_path: Path) -> None:
    selected = components(tmp_path, "--stdin", stdin="vendoo-studio/server/vendoo_studio/routes/listings.py\n")
    assert selected["backend"] == "true"
    assert selected["browser"] == "true"
    assert selected["frontend"] == "false"
    assert selected["audit"] == "false"


def test_test_only_change_does_not_run_browser_suites(tmp_path: Path) -> None:
    selected = components(tmp_path, "--stdin", stdin="vendoo-studio/server/tests/test_listing.py\n")
    assert selected["backend"] == "true"
    assert selected["health"] == "true"
    assert selected["browser"] == "false"
    assert selected["frontend"] == "false"


def test_a_rename_runs_both_components(repository: tuple[Path, str]) -> None:
    root, _ = repository
    source = root / "vendoo-studio" / "src" / "moved.ts"
    source.parent.mkdir()
    source.write_text("export const value = 1;\n", encoding="utf-8")
    git(root, "add", ".")
    git(root, "commit", "-qm", "Add source")
    base = git(root, "rev-parse", "HEAD")
    destination = root / "webhook-relay" / "moved.ts"
    destination.parent.mkdir()
    git(root, "mv", str(source), str(destination))
    git(root, "commit", "-qm", "Move source to relay")
    assert components(root, "--base", base) == flags("frontend", "backend", "browser", "health", "relay")
