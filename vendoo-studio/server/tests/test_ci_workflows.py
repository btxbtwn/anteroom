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


@pytest.fixture
def main_since_production(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> dict[str, str]:
    """main's first-parent line: production's release, two merges, and their release."""

    def commit(message: str) -> str:
        subprocess.run(["git", "commit", "-q", "--allow-empty", "-m", message], cwd=tmp_path, check=True)
        return subprocess.run(["git", "rev-parse", "HEAD"], cwd=tmp_path, check=True, capture_output=True, text=True).stdout.strip()

    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    subprocess.run(["git", "config", "user.email", "t@example.com"], cwd=tmp_path, check=True)
    subprocess.run(["git", "config", "user.name", "t"], cwd=tmp_path, check=True)
    shas = {"old": commit("Merge pull request #0 from x/y")}
    shas["production"] = commit("Release Anteroom 0.1.1")
    shas["m1"] = commit("Merge pull request #1 from x/app-change")
    shas["m2"] = commit("Merge pull request #2 from x/docs-only")
    shas["release"] = commit("Release Anteroom 0.1.2")
    monkeypatch.chdir(tmp_path)
    return shas


def test_every_merge_since_production_is_checked(main_since_production: dict[str, str]) -> None:
    shas = main_since_production
    gate = _load_gate()
    assert gate.commits_to_check(shas["release"], shas["production"])[0] == [shas["m1"], shas["m2"]]
    # A manual run of a merge checks that merge too.
    assert gate.commits_to_check(shas["m2"], shas["production"])[0] == [shas["m1"], shas["m2"]]
    # Production's commit unknown, or not below the one being shipped: the nearest merge only.
    assert gate.commits_to_check(shas["release"], None)[0] == [shas["m2"]]
    assert gate.commits_to_check(shas["release"], "f" * 40)[0] == [shas["m2"]]
    assert gate.commits_to_check(shas["production"], shas["release"])[0] == [shas["old"]]


@pytest.mark.parametrize(
    ("head", "production", "ci", "passes"),
    [
        # M1 failed and docs-only M2 passed: M2's release still ships M1's code.
        ("release", "production", {"m1": "failure", "m2": "success"}, False),
        # A re-run of M1's CI passed: its latest attempt is what counts.
        ("release", "production", {"m1": "success", "m2": "success"}, True),
        # A manual run of a merge whose own CI failed.
        ("m2", "production", {"m1": "success", "m2": "failure"}, False),
        ("m2", "production", {"m1": "cancelled", "m2": "success"}, False),
        # No record of production's commit: only the nearest merge, as before.
        ("release", None, {"m1": "failure", "m2": "success"}, True),
    ],
)
def test_deploy_needs_ci_on_every_merge_since_production(
    main_since_production: dict[str, str], monkeypatch: pytest.MonkeyPatch, capsys, head, production, ci, passes
) -> None:
    shas = main_since_production
    gate = _load_gate()
    by_sha = {shas[name]: ("completed", conclusion) for name, conclusion in ci.items()}
    monkeypatch.setattr(sys, "argv", ["gate", "--repo", "o/r", "--head", shas[head], "--timeout", "0"])
    monkeypatch.setattr(gate, "production_commit", lambda repo: shas[production] if production else None)
    monkeypatch.setattr(gate, "ci_state", lambda repo, sha: by_sha[sha])
    assert gate.main() == (0 if passes else 1)
    if production is None:
        assert "nearest merge" in capsys.readouterr().out


def test_production_commit_is_the_last_recorded_deploy(monkeypatch: pytest.MonkeyPatch) -> None:
    gate = _load_gate()
    deployed = "d" * 40
    responses = {
        "repos/o/r/actions/workflows/deploy-hosted.yml/runs?status=success&per_page=50": {
            "workflow_runs": [{"id": 3, "head_sha": "h" * 40}, {"id": 2, "head_sha": "g" * 40}]
        },
        # Run 3 skipped at the gate: nothing deployed, so no record.
        "repos/o/r/actions/runs/3/jobs": {"jobs": [{"id": 30}]},
        "repos/o/r/check-runs/30/annotations": [{"title": None, "message": "Nothing to deploy"}],
        "repos/o/r/actions/runs/2/jobs": {"jobs": [{"id": 20}]},
        "repos/o/r/check-runs/20/annotations": [{"title": "Deployed commit", "message": deployed}],
    }
    monkeypatch.setattr(gate, "gh_api", responses.__getitem__)
    assert gate.production_commit("o/r") == deployed
    responses["repos/o/r/check-runs/20/annotations"] = []
    assert gate.production_commit("o/r") is None


def test_deploy_records_the_commit_it_shipped() -> None:
    steps = workflow("deploy-hosted.yml")["jobs"]["deploy"]["steps"]
    assert steps[-1]["name"] == "Record the deployed commit"
    assert "::notice title=Deployed commit::" in steps[-1]["run"]


def test_only_runs_that_deploy_join_the_deploy_queue() -> None:
    deploy = workflow("deploy-hosted.yml")
    # A CI completion whose deploy job is skipped must not replace a queued deploy.
    assert "concurrency" not in deploy
    assert deploy["jobs"]["deploy"]["concurrency"] == {"group": "deploy-hosted", "cancel-in-progress": "false"}


def _load_script(name: str):
    import importlib.util

    spec = importlib.util.spec_from_file_location(name.replace("-", "_"), ROOT / ".github" / "scripts" / f"{name}.py")
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_deploy_fetches_main_and_gates_it_before_anything_else() -> None:
    steps = workflow("deploy-hosted.yml")["jobs"]["deploy"]["steps"]
    assert steps[0]["with"]["ref"] == "main"
    assert steps[0]["with"]["persist-credentials"] == "false"
    assert "deploy-gate.py" in steps[1]["run"]
    assert all(step.get("if") == "steps.gate.outputs.skip != 'true'" for step in steps[2:])


T, M, R, N = "t" * 40, "m" * 40, "r" * 40, "n" * 40
OK = ("completed", "success")


@pytest.mark.parametrize(
    ("commits", "notes", "ci", "chosen"),
    [
        ([T], set(), {T: OK}, T),  # the trigger is still main's tip
        ([T], {T}, {}, None),  # pending notes: the release commit deploys
        ([R, M, T], {M}, {R: OK}, R),  # the newest tested commit wins
        # main moved on but its CI is still running: ship the newest tested one
        # anyway rather than wait (merges can outpace CI), never anything older.
        ([R, M, T], {M}, {R: ("queued", ""), T: OK}, T),
        ([N, R, M, T], {M}, {N: ("in_progress", ""), R: OK}, R),
        ([R, M, T], {M, T}, {R: ("completed", "failure")}, None),
        ([R, T], set(), {R: ("missing", ""), T: OK}, T),
        # A manual run starts from main's tip, whose CI nothing has checked yet.
        ([T], set(), {T: ("completed", "failure")}, None),
        ([T], set(), {T: ("in_progress", "")}, None),
    ],
)
def test_deploy_gate_ships_the_newest_tested_commit_from_the_trigger_up(commits, notes, ci, chosen) -> None:
    gate = _load_script("deploy-gate")
    assert gate.choose(commits, notes.__contains__, ci.__getitem__)[0] == chosen


def test_deploy_gate_checks_out_its_choice_and_writes_outputs(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    (repo / "vendoo-studio").mkdir(parents=True)
    (repo / "changelog.d").mkdir()
    (repo / "changelog.d" / "README.md").write_text("notes\n")

    def git(*args: str) -> str:
        return subprocess.run(
            ["git", "-c", "user.name=t", "-c", "user.email=t@example.com", "-c", "commit.gpgsign=false", *args],
            cwd=repo, check=True, capture_output=True, text=True,
        ).stdout.strip()

    def commit(version: str, message: str, note: bool = False) -> str:
        (repo / "vendoo-studio" / "VERSION").write_text(version + "\n")
        (repo / "changelog.d" / "fix.md").unlink(missing_ok=True)
        if note:
            (repo / "changelog.d" / "fix.md").write_text("- Fix.\n")
        git("add", "-A")
        git("commit", "-qm", message)
        return git("rev-parse", "HEAD")

    git("init", "-q")
    trigger = commit("1.0.0", "Release Anteroom 1.0.0")
    commit("1.0.0", "Merge pull request #2", note=True)
    release = commit("1.0.1", "Release Anteroom 1.0.1")

    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    ci_status = tmp_path / "ci-status"
    # Answers each commit's CI query from {sha: "status conclusion"}.
    (bin_dir / "gh").write_text(
        f"#!{sys.executable}\nimport json, pathlib, re, sys\n"
        "sha = re.search('head_sha=([0-9a-f]+)', sys.argv[2]).group(1)\n"
        f"status, conclusion = json.loads(pathlib.Path({str(ci_status)!r}).read_text())[sha].split()\n"
        "print(json.dumps({'workflow_runs': [{'status': status, 'conclusion': conclusion}]}))\n"
    )
    (bin_dir / "gh").chmod(0o755)

    def run(release_ci: str, trigger_ci: str = "completed success") -> tuple[str, str]:
        git("checkout", "-q", "--detach", release)
        ci_status.write_text(json.dumps({release: release_ci, trigger: trigger_ci}))
        output = tmp_path / "output"
        output.write_text("")
        subprocess.run(
            [sys.executable, str(ROOT / ".github" / "scripts" / "deploy-gate.py"), "--repo", "o/r", "--trigger", trigger],
            cwd=repo, check=True, capture_output=True,
            env={**os.environ, "GITHUB_OUTPUT": str(output), "PATH": f"{bin_dir}{os.pathsep}{os.environ['PATH']}"},
        )
        return output.read_text(), git("rev-parse", "HEAD")

    assert run("completed success") == ("skip=false\nversion=1.0.1\n", release)
    # The release commit's CI is still queued: ship the trigger, never wait.
    assert run("queued none") == ("skip=false\nversion=1.0.0\n", trigger)
    # A manual run is not proof that CI passed: nothing tested ships nothing.
    assert run("queued none", "completed failure") == ("skip=true\n", release)


def _machine(group: str | None, tag: str, state: str = "started", **config) -> dict:
    metadata = {"fly_process_group": group} if group else {}
    return {"id": f"{group}-{tag}", "state": state, "image_ref": {"tag": tag}, "config": {"metadata": metadata, **config}}


@pytest.mark.parametrize(
    ("machines", "passes"),
    [
        ([_machine("app", "v1-new")], True),
        ([_machine("app", "v1-new"), _machine("worker", "v1-new")], True),
        ([_machine("app", "v1-new"), _machine("worker", "v0-old")], False),
        ([_machine("app", "v1-new"), _machine("worker", "v1-new", state="stopped")], False),
        ([_machine("app", "v1-new"), _machine("app", "v0-old", auto_destroy=True)], True),
        ([_machine("fly_app_console", "v0-old"), _machine(None, "v1-new")], True),
        ([], False),
    ],
)
def test_every_machine_in_every_process_group_must_run_the_new_image(machines: list, passes: bool) -> None:
    check = _load_script("check-fly-machines")
    assert (check.problems(machines, "v1-new")[1] == []) is passes


def test_machine_image_falls_back_to_the_configured_image() -> None:
    check = _load_script("check-fly-machines")
    assert check.image_tag({"config": {"image": "registry.fly.io/app:v1-new@sha256:abc"}}) == "v1-new"
