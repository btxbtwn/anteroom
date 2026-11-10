"""Exercise the CI gate and local check selection without running seller code."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[3]


def workflow(name: str) -> dict:
    return yaml.load((ROOT / ".github" / "workflows" / name).read_text(), Loader=yaml.BaseLoader)


@pytest.mark.parametrize(
    ("changes", "results", "passes"),
    [
        ("success", "success success skipped", True),
        ("success", "success failure skipped", False),
        ("success", "success cancelled skipped", False),
        ("failure", "failure skipped skipped", False),
        ("skipped", "skipped skipped skipped", False),
    ],
)
def test_ci_gate_rejects_failures_and_accepts_intentional_skips(changes: str, results: str, passes: bool) -> None:
    gate = workflow("ci.yml")["jobs"]["ci"]["steps"][0]
    result = subprocess.run(
        ["bash", "-e", "-c", gate["run"]],
        env={**os.environ, "CHANGES": changes, "RESULTS": results},
        capture_output=True,
        text=True,
    )
    assert (result.returncode == 0) == passes, result.stdout + result.stderr


def test_every_selected_job_is_wired_into_the_required_gate() -> None:
    jobs = workflow("ci.yml")["jobs"]
    selected_jobs = {name for name, job in jobs.items() if "needs.changes.outputs." in job.get("if", "")}
    assert selected_jobs <= set(jobs["ci"]["needs"])
    outputs = jobs["changes"]["outputs"]
    for name in selected_jobs:
        group = jobs[name]["if"].split("needs.changes.outputs.")[1].split()[0]
        assert group in outputs
    assert jobs["dependency-audit"]["uses"] == "./.github/workflows/dependency-audit.yml"


def test_audits_run_daily_and_can_be_called_by_dependency_prs() -> None:
    audit = workflow("dependency-audit.yml")
    assert "workflow_call" in audit["on"]
    assert "workflow_dispatch" in audit["on"]
    assert audit["on"]["schedule"][0]["cron"] == "23 8 * * *"
    assert audit["permissions"] == {"contents": "read"}


def test_selector_failure_cannot_be_hidden_by_output_capture(tmp_path: Path) -> None:
    script = tmp_path / ".github" / "scripts" / "changed-components.py"
    script.parent.mkdir(parents=True)
    script.write_text("raise SystemExit(7)\n")
    step = next(step for step in workflow("ci.yml")["jobs"]["changes"]["steps"] if step.get("id") == "changes")
    result = subprocess.run(
        ["bash", "-e", "-c", step["run"]],
        cwd=tmp_path,
        env={**os.environ, "GITHUB_EVENT_NAME": "workflow_dispatch", "GITHUB_OUTPUT": str(tmp_path / "output")},
        capture_output=True,
        text=True,
    )
    assert result.returncode == 7, result.stdout + result.stderr


@pytest.mark.parametrize(
    ("changed", "frontend", "backend", "browser", "audit"),
    [
        ("vendoo-studio/server/vendoo_studio/routes/example.py", False, True, True, False),
        ("vendoo-studio/server/tests/test_example.py", False, True, False, False),
        ("vendoo-studio/src/App.tsx", True, True, True, False),
        ("vendoo-studio/uv.lock", False, True, True, True),
        ("docs/example.md", False, False, False, False),
    ],
)
def test_local_check_driver_uses_selected_groups(
    tmp_path: Path, changed: str, frontend: bool, backend: bool, browser: bool, audit: bool,
) -> None:
    repo = tmp_path / "repo"
    scripts = repo / ".github" / "scripts"
    scripts.mkdir(parents=True)
    shutil.copy(ROOT / "check.sh", repo)
    shutil.copy(ROOT / ".github" / "scripts" / "changed-components.py", scripts)

    def git(*args: str) -> None:
        subprocess.run(["git", *args], cwd=repo, check=True, capture_output=True)

    git("init", "-q")
    git("config", "user.name", "Test")
    git("config", "user.email", "test@example.invalid")
    git("config", "commit.gpgsign", "false")
    git("add", ".")
    git("commit", "-qm", "Initial")
    git("update-ref", "refs/remotes/origin/main", "HEAD")
    target = repo / changed
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text("test input\n")

    # Execute the real selector and shell driver. Record only their requested
    # check commands; this fixture has no app, provider or seller database.
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    commands = tmp_path / "commands.jsonl"
    shell = bin_dir / "bash"
    shell.write_text(
        f"#!{sys.executable}\nimport json, os, sys\n"
        "with open(os.environ['CHECK_COMMANDS'], 'a') as out:\n"
        "    out.write(json.dumps(sys.argv[1:]) + '\\n')\n"
    )
    shell.chmod(0o755)
    # Keep tools deterministic across machines; their work is outside this test.
    for name in ("uv", "gitleaks"):
        tool = bin_dir / name
        tool.write_text("#!/bin/sh\nexit 0\n")
        tool.chmod(0o755)
    result = subprocess.run(
        ["/bin/bash", str(repo / "check.sh")],
        env={**os.environ, "PATH": str(bin_dir) + os.pathsep + os.environ["PATH"],
             "CHECK_COMMANDS": str(commands), "ANTEROOM_TEST_DATABASE_URL": ""},
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    invoked = "\n".join(json.loads(line)[1] for line in commands.read_text().splitlines())
    assert ("npm run build" in invoked) == frontend
    assert ("-m pytest" in invoked) == backend
    assert ("npm run e2e" in invoked) == browser
    assert ("-m pip_audit" in invoked) == audit


def test_pushes_to_main_never_cancel_each_others_ci() -> None:
    concurrency = workflow("ci.yml")["concurrency"]
    assert "github.sha" in concurrency["group"]
    assert concurrency["cancel-in-progress"] == "${{ github.event_name == 'pull_request' }}"


def test_deploy_builds_and_waits_for_tested_code_before_migrating() -> None:
    steps = [step.get("name", step.get("uses", "")) for step in workflow("deploy-hosted.yml")["jobs"]["deploy"]["steps"]]
    wait = steps.index("Wait for CI on the code being shipped")
    build = steps.index("Build the image")
    migrate = steps.index("Apply pending migrations")
    deploy = steps.index("Deploy to Fly.io")
    assert wait < build < migrate < deploy


def _load_gate():
    import importlib.util

    spec = importlib.util.spec_from_file_location("wait_for_tested_code", ROOT / ".github" / "scripts" / "wait-for-tested-code.py")
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_release_commits_resolve_to_the_merge_they_ship(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    def commit(message: str) -> str:
        subprocess.run(["git", "commit", "-q", "--allow-empty", "-m", message], cwd=tmp_path, check=True)
        return subprocess.run(["git", "rev-parse", "HEAD"], cwd=tmp_path, check=True, capture_output=True, text=True).stdout.strip()

    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    subprocess.run(["git", "config", "user.email", "t@example.com"], cwd=tmp_path, check=True)
    subprocess.run(["git", "config", "user.name", "t"], cwd=tmp_path, check=True)
    commit("Release Anteroom 0.1.1")
    merge = commit("Merge pull request #1 from x/y")
    release = commit("Release Anteroom 0.1.2")
    monkeypatch.chdir(tmp_path)
    gate = _load_gate()
    assert gate.code_commit(release) == merge
    assert gate.code_commit(merge) == merge


@pytest.mark.parametrize("release", [False, True])
@pytest.mark.parametrize("conclusion", ["success", "failure", "cancelled", "skipped"])
def test_deploy_checks_ci_even_for_a_manual_code_commit(monkeypatch, release, conclusion) -> None:
    gate = _load_gate()
    head = "release-head" if release else "code-head"
    queried = []
    monkeypatch.setattr(sys, "argv", ["gate", "--repo", "example/repo"])
    monkeypatch.setattr(gate, "git", lambda *args: head)
    monkeypatch.setattr(gate, "code_commit", lambda sha: "code-head")

    def ci_state(repo, sha):
        queried.append((repo, sha))
        return "completed", conclusion

    monkeypatch.setattr(gate, "ci_state", ci_state)
    assert gate.main() == (0 if conclusion == "success" else 1)
    assert queried == [("example/repo", "code-head")]


@pytest.mark.parametrize("status", ["missing", "queued", "in_progress"])
def test_manual_deploy_times_out_without_successful_ci(monkeypatch, status) -> None:
    gate = _load_gate()
    monkeypatch.setattr(sys, "argv", ["gate", "--repo", "example/repo", "--timeout", "0"])
    monkeypatch.setattr(gate, "git", lambda *args: "code-head")
    monkeypatch.setattr(gate, "code_commit", lambda sha: sha)
    monkeypatch.setattr(gate, "ci_state", lambda repo, sha: (status, ""))
    ticks = iter([1, 2, 3, 4])
    monkeypatch.setattr(gate.time, "monotonic", lambda: next(ticks))
    assert gate.main() == 1
