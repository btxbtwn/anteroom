"""The iPhone workflow uploads to TestFlight once per release, with its new version."""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[3]


def workflow(name: str) -> dict:
    return yaml.load((ROOT / ".github" / "workflows" / name).read_text(), Loader=yaml.BaseLoader)


def test_testflight_waits_for_the_release_commit_and_is_never_cancelled_on_main() -> None:
    ios = workflow("ios.yml")
    assert "vendoo-studio/VERSION" in ios["on"]["push"]["paths"]
    assert ios["concurrency"]["cancel-in-progress"] == "${{ github.event_name == 'pull_request' }}"
    for name in ("build", "sandbox-tests"):
        assert ios["jobs"][name]["needs"] == "gate"
        assert ios["jobs"][name]["if"] == "needs.gate.outputs.run == 'true'"
    assert set(ios["jobs"]["testflight"]["needs"]) == {"build", "sandbox-tests"}


@pytest.mark.parametrize(
    ("event", "history", "runs"),
    [
        ("pull_request", [("Merge ios", "anteroom-ios/a.swift", True)], True),
        # A merge with notes: the release commit after it uploads the new version.
        ("push", [("Merge ios", "anteroom-ios/a.swift", True)], False),
        ("push", [("Merge ios", "anteroom-ios/a.swift", False)], True),
        # A release commit uploads only when iPhone code changed since the previous release.
        ("push", [("Merge ios", "anteroom-ios/a.swift", True), ("Release Anteroom 0.1.2", None, False)], True),
        ("push", [("Merge web", "vendoo-studio/a.py", True), ("Release Anteroom 0.1.2", None, False)], False),
    ],
)
def test_ios_gate_builds_only_what_testflight_should_upload(tmp_path: Path, event: str, history: list, runs: bool) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()

    def git(*args: str) -> None:
        subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@example.com", "-c", "commit.gpgsign=false", *args],
                       cwd=repo, check=True, capture_output=True)

    git("init", "-q")
    (repo / "changelog.d").mkdir()
    (repo / "changelog.d" / "README.md").write_text("notes\n")
    git("add", ".")
    git("commit", "-qm", "Release Anteroom 0.1.1")
    for message, path, note in history:
        if path:
            (repo / path).parent.mkdir(parents=True, exist_ok=True)
            (repo / path).write_text(message)
        (repo / "changelog.d" / "fix.md").unlink(missing_ok=True)
        if note:
            (repo / "changelog.d" / "fix.md").write_text("- Fix.\n")
        git("add", "-A")
        git("commit", "-qm", message)
    step = workflow("ios.yml")["jobs"]["gate"]["steps"][1]
    output = tmp_path / "output"
    result = subprocess.run(
        ["bash", "-e", "-c", step["run"]],
        cwd=repo, capture_output=True, text=True,
        env={**os.environ, "GITHUB_EVENT_NAME": event, "GITHUB_OUTPUT": str(output)},
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert output.read_text() == f"run={'true' if runs else 'false'}\n"
