#!/usr/bin/env python3
"""Wait until CI has passed on every merge a deploy is about to ship.

CI on main picks its jobs from the change against the previous commit, so a
docs-only merge can pass while an earlier merge's code failed. Shipping the
newer one ships both. So every first-parent commit on main after the one
production runs, up to the one being shipped, must have passed CI. When GitHub
pushes multiple commits to main in one push (for example a rebase merge), GitHub
Actions triggers push CI only on the push tip with ``github.event.before`` set
to the commit before the push; an intermediate commit without its own push run
may count as tested only when a later push run at the tip is live-verified to
have the exact trusted push provenance, the exact same ``base..tip`` Git diff
and commit sequence, and every required CI job for that diff passing. "Release
Anteroom" commits from the Version workflow only change version numbers and
their CI skips the component checks, so they are left out; the merge before
one can still be running when the release commit's CI passes, so this waits.

The commit production runs is the one the last successful Deploy hosted run
recorded with a "Deployed commit" notice. Without one, only the nearest merge
is checked. It exits 1 if a check fails, is cancelled, never appears or does
not finish in time.

When recorded and live production are still on 23f8ab01 (0.1.1211), the
three push CI failures caused by the split Alembic history (#2032 / #2031 /
#2040; fixed in #2045 / afc3c2db) may be superseded only after all other
commits in the span pass push CI and an exact Descendant verification receipt
is live-verified against GitHub and local Git. Until a new receipt is pinned
in HISTORICAL_RECOVERY_CHECKPOINT, those failures block every deploy. Once
Deploy hosted succeeds and records a newer Deployed commit, the checkpoint
never matches again and ordinary gating applies unchanged.
"""

from __future__ import annotations

import argparse
import http.client
import importlib.util
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

_SCRIPTS_DIR = str(Path(__file__).resolve().parent)
if _SCRIPTS_DIR not in sys.path:
    sys.path.insert(0, _SCRIPTS_DIR)

import recovery_evidence
import verification_evidence

RELEASE_PREFIX = "Release Anteroom "
DEPLOYED_TITLE = "Deployed commit"
MAX_HEALTH_BYTES = 64 * 1024
MAX_COMPARE_BYTES = 16 * 1024 * 1024
REPO_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]*/[A-Za-z0-9][A-Za-z0-9_.-]*")
CI_WORKFLOW_PATH = ".github/workflows/ci.yml"
CI_ALWAYS_REQUIRED_JOBS = frozenset({"Changed components", "Changelog", "Repo checks", "CI"})
CI_COMPONENT_JOBS: dict[str, tuple[str, ...]] = {
    "frontend": ("Studio frontend",),
    "backend": (
        "Studio backend (1/2)",
        "Studio backend (2/2)",
        "Studio backend Postgres (1/2)",
        "Studio backend Postgres (2/2)",
    ),
    "browser": ("Studio browser tests (sandbox)", "Studio browser tests (hosted)"),
    "health": ("Studio code health",),
    "audit": ("Dependency audit / Audit locked Python dependencies",),
    "extension": ("Extension",),
    "relay": ("Webhook relay",),
}
PRE_RENAME_PREFIXES = (
    ("vendoo-studio/", "anteroom/"),
    ("vendoo-extension/", "anteroom-extension/"),
    ("vendoo-ios/", "anteroom-ios/"),
)

# These pushes all tested the same split Alembic history. #2045 joined both
# heads. Pin a Descendant verification receipt (production_base=23f8ab01)
# before Deploy hosted can supersede them; None fails closed.
HISTORICAL_RECOVERY_FAILURES = frozenset({
    "99c3b7003d1d79cfc20cea283bebde7e54572081",  # CI 45390465842
    "08b97e99edaa02f021bc7bcf876d2fd2ade83c69",  # CI 45391561313
    "fb7d5c076cce6021b48240014ba6fb0e71256300",  # CI 45394131194
})
HISTORICAL_RECOVERY_CORRECTION = "afc3c2dbde647b59203c0b761c8c0dfb2b697264"

# Spent d19adbff / 0.1.1091 pin removed after production reached 23f8ab01.
# Pin a Descendant verification receipt here before Deploy hosted can
# supersede HISTORICAL_RECOVERY_FAILURES; None fails closed.
HISTORICAL_RECOVERY_CHECKPOINT: recovery_evidence.EvidencePin | None = None
HISTORICAL_BASE_FLY_APP = "peaceful-meadowstone-6944"
HISTORICAL_BASE_IMAGE_DIGEST = "sha256:22df137ee877807504d7223d6aff53e8cee6777cf2ddfd70838a14a822641591"
HISTORICAL_BASE_DB_REVISION = "e3b7a1c9d402"
HISTORICAL_BASE_FLY_GROUPS = ("app", "worker")
ALEMBIC_CURRENT_RE = re.compile(r"([0-9a-f]{12})(?: \(head\))?")
FLY_APP_RE = re.compile(r"[a-z0-9][a-z0-9-]*")


def _load_fly_machine_checker():
    spec = importlib.util.spec_from_file_location(
        "check_fly_machines",
        Path(__file__).resolve().parent / "check-fly-machines.py",
    )
    if spec is None or spec.loader is None:
        raise RuntimeError("Could not load check-fly-machines.py.")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _write_github_output(name: str, value: str) -> None:
    path = os.environ.get("GITHUB_OUTPUT")
    if path:
        with open(path, "a", encoding="utf-8") as handle:
            handle.write(f"{name}={value}\n")


def git(*args: str) -> str:
    return subprocess.run(["git", *args], check=True, capture_output=True, text=True).stdout.strip()


def gh_api(path: str):
    return json.loads(subprocess.run(["gh", "api", path], check=True, capture_output=True, text=True).stdout)


def is_release(sha: str) -> bool:
    return git("log", "-1", "--format=%s", sha).startswith(RELEASE_PREFIX)


def code_commit(head: str) -> str:
    """The nearest first-parent commit at or before head that is not a release commit."""
    sha = git("rev-parse", head)
    while is_release(sha):
        sha = git("rev-parse", f"{sha}^")
    return sha


def production_commit(repo: str, limit: int = 50) -> str | None:
    """The commit the newest successful deploy recorded, or None.

    A run's head_sha is main's tip when it was queued, not what it shipped, and
    runs that skip at the gate succeed too, so the deploy records its commit.
    """
    runs = gh_api(f"repos/{repo}/actions/workflows/deploy-hosted.yml/runs?status=success&per_page={limit}")
    for run in runs["workflow_runs"]:
        for job in gh_api(f"repos/{repo}/actions/runs/{run['id']}/jobs")["jobs"]:
            for note in gh_api(f"repos/{repo}/check-runs/{job['id']}/annotations"):
                if note.get("title") == DEPLOYED_TITLE:
                    return note["message"].strip()
    return None


def commits_to_check(head: str, production: str | None) -> tuple[list[str], str]:
    """(code commits after production up to head, oldest first; why)."""
    head = git("rev-parse", head)
    if production is None:
        why = "No deploy recorded which commit production runs"
    elif production == head:
        why = f"Production already runs {head[:8]}"
    elif subprocess.run(["git", "merge-base", "--is-ancestor", production, head], capture_output=True).returncode:
        why = f"Production's commit {production[:8]} is not below {head[:8]} on main"
    else:
        span = git("rev-list", "--first-parent", "--reverse", f"{production}..{head}").splitlines()
        shas = [sha for sha in span if not is_release(sha)]
        listed = ", ".join(sha[:8] for sha in shas) or "none"
        return shas, f"Production runs {production[:8]}; checking CI on every merge since: {listed}."
    nearest = code_commit(head)
    return [nearest], f"{why}; checking only the nearest merge, {nearest[:8]}."


def ci_state(repo: str, sha: str) -> tuple[str, str]:
    """(status, conclusion) of the latest CI run for a push of sha, or ("missing", "").

    A re-run updates the same run, so this is its latest attempt's result.
    """
    out = subprocess.run(
        ["gh", "api", f"repos/{repo}/actions/workflows/ci.yml/runs?head_sha={sha}&event=push&per_page=1"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout
    runs = json.loads(out)["workflow_runs"]
    if not runs:
        return "missing", ""
    return runs[0]["status"], runs[0]["conclusion"] or ""


def live_health_version(url: str) -> str:
    """Read the live hosted web version from HTTPS HEALTH_URL; fail closed on any mismatch."""
    parsed = urllib.parse.urlparse(url) if isinstance(url, str) else None
    if not parsed or parsed.scheme != "https" or not parsed.netloc:
        raise ValueError("An explicit HTTPS HEALTH_URL is required to corroborate live production.")
    request = urllib.request.Request(
        url,
        headers={"Accept": "application/json", "User-Agent": "anteroom-deploy-gate"},
    )
    try:
        with urllib.request.urlopen(request, timeout=15) as response:
            final = urllib.parse.urlparse(response.geturl())
            if final.scheme != "https" or final.netloc != parsed.netloc:
                raise ValueError("Live health endpoint redirected off its expected HTTPS host.")
            if response.status != 200:
                raise ValueError(f"Live health endpoint returned HTTP {response.status}.")
            raw = response.read(MAX_HEALTH_BYTES + 1)
    except (urllib.error.URLError, http.client.HTTPException, TimeoutError, OSError) as error:
        raise ValueError("Live health endpoint could not be read.") from error
    if len(raw) > MAX_HEALTH_BYTES:
        raise ValueError("Live health response exceeded its size bound.")
    payload = json.loads(
        raw,
        object_pairs_hook=recovery_evidence.unique_object,
        parse_constant=lambda _: (_ for _ in ()).throw(ValueError("Non-finite JSON.")),
    )
    if (
        not isinstance(payload, dict)
        or payload.get("status") != "ok"
        or payload.get("service") != "anteroom-hosted"
        or not isinstance(payload.get("version"), str)
        or not payload["version"].strip()
    ):
        raise ValueError("Live health payload did not report a healthy anteroom-hosted version.")
    return payload["version"].strip()


def _read_git(*args: str) -> str:
    return recovery_evidence.read_command(["git", *args]).decode("utf-8", errors="surrogateescape").rstrip("\n")


def checkpoint_eligible(
    repo: str,
    head: str,
    production: str | None,
    failed_sha: str,
    pin: recovery_evidence.EvidencePin | None = HISTORICAL_RECOVERY_CHECKPOINT,
    git_fn=None,
) -> tuple[bool, str]:
    """Cheap local fail-closed eligibility check before waiting on later commits."""
    if pin is None:
        return False, "No recovery checkpoint is configured."
    if repo != pin.repository:
        return False, f"Repository {repo!r} does not match pinned repository {pin.repository!r}."
    if production is None or production != pin.production_base:
        return False, "Recorded production commit does not match the pinned recovery base."
    if failed_sha not in HISTORICAL_RECOVERY_FAILURES:
        return False, f"Commit {failed_sha[:8]} is outside the diagnosed historical failures."
    run_git = git_fn or _read_git
    resolved_head = run_git("rev-parse", head)
    main_line = run_git("rev-list", "--first-parent", "origin/main").splitlines()
    if resolved_head not in main_line:
        return False, f"Target {resolved_head[:8]} is not on origin/main first-parent history."
    span = run_git("rev-list", "--first-parent", "--reverse", f"{production}..{resolved_head}").splitlines()
    if HISTORICAL_RECOVERY_CORRECTION not in span or pin.target not in span:
        return False, "The correcting merge and verified checkpoint must follow production at or before HEAD."
    correction_index = span.index(HISTORICAL_RECOVERY_CORRECTION)
    if (
        not HISTORICAL_RECOVERY_FAILURES.issubset(span[:correction_index])
        or correction_index > span.index(pin.target)
    ):
        return False, "Every diagnosed failure must precede the correcting merge and verified checkpoint on main."
    return True, ""


def _version_at(run_git, sha: str) -> str:
    """Read VERSION at a commit, falling back to the pre-rename path for historical commits."""
    for path in ("anteroom/VERSION", "vendoo-studio/VERSION"):
        try:
            version = run_git("show", f"{sha}:{path}").strip()
        except (KeyError, RuntimeError, subprocess.SubprocessError):
            continue
        if version:
            return version
    return ""


def verify_historical_checkpoint(
    repo: str,
    head: str,
    production: str | None,
    failed_sha: str,
    *,
    pin: recovery_evidence.EvidencePin | None = HISTORICAL_RECOVERY_CHECKPOINT,
    health_url: str = "",
    fetch=None,
    download=None,
    git_fn=None,
    live_version_fn=None,
) -> dict:
    """Live-verify the pinned receipt and live production base before allowing recovery."""
    run_git = git_fn or _read_git
    eligible, reason = checkpoint_eligible(repo, head, production, failed_sha, pin=pin, git_fn=run_git)
    if not eligible:
        raise ValueError(reason)
    assert pin is not None

    expected_version = _version_at(run_git, pin.production_base)
    if not expected_version:
        raise ValueError("Could not read expected VERSION for the pinned production base.")
    live_version = (live_version_fn or live_health_version)(health_url)
    if live_version != expected_version:
        raise ValueError(
            f"Live production version {live_version!r} does not match pinned base "
            f"{pin.production_base[:8]} version {expected_version!r}."
        )

    def default_fetch(path: str):
        return json.loads(
            recovery_evidence.read_command(["gh", "api", "--method", "GET", f"repos/{pin.repository}/{path}"])
        )

    def default_download(artifact_id: int) -> bytes:
        return recovery_evidence.read_command(
            ["gh", "api", "--method", "GET", f"repos/{pin.repository}/actions/artifacts/{artifact_id}/zip"]
        )

    report = recovery_evidence.verify(pin, fetch or default_fetch, download or default_download, run_git)
    if (
        report.get("target") != pin.target
        or report.get("production_base") != pin.production_base
        or report.get("run_id") != pin.run_id
        or report.get("attempt") != pin.attempt
        or report.get("receipt_artifact_id") != pin.receipt_artifact_id
        or report.get("receipt_digest") != pin.receipt_digest
    ):
        raise ValueError("Verified receipt report did not match the pinned checkpoint.")
    return {**report, "live_base_version": live_version}


def parse_alembic_current(output: str) -> str:
    """Parse stdout of `python -m alembic current`; require a single unambiguous revision."""
    lines = [line.strip() for line in output.splitlines() if line.strip()]
    if len(lines) != 1:
        raise ValueError(f"Expected exactly one live Alembic revision line, got {len(lines)}.")
    match = ALEMBIC_CURRENT_RE.fullmatch(lines[0])
    if not match:
        raise ValueError(f"Unrecognized live Alembic current output: {lines[0]!r}.")
    return match.group(1)


def live_fly_machines(fly_app: str) -> list[dict]:
    """Read live Fly Machines JSON via bounded read-only flyctl command without logging stderr."""
    if not isinstance(fly_app, str) or not FLY_APP_RE.fullmatch(fly_app):
        raise ValueError(f"Invalid Fly app name {fly_app!r}.")
    raw = recovery_evidence.read_command(["flyctl", "machines", "list", "--app", fly_app, "--json"])
    payload = json.loads(
        raw,
        object_pairs_hook=recovery_evidence.unique_object,
        parse_constant=lambda _: (_ for _ in ()).throw(ValueError("Non-finite JSON.")),
    )
    if not isinstance(payload, list):
        raise ValueError("Live Fly Machines payload must be a JSON list.")
    return payload


def live_alembic_revision(
    alembic_cwd: str | Path,
    *,
    env: dict[str, str] | None = None,
    retries: int = 3,
    retry_delay: float = 15.0,
) -> str:
    """Read live hosted Postgres Alembic revision without logging credentials or raw stderr."""
    source_env = dict(os.environ if env is None else env)
    db_url = source_env.get("ANTEROOM_DATABASE_URL", "")
    if not db_url.startswith(("postgres://", "postgresql://", "postgresql+psycopg://")):
        raise ValueError("A Postgres ANTEROOM_DATABASE_URL is required to reconcile live DB schema.")
    if source_env.get("PGSSLMODE") != "verify-full":
        raise ValueError("PGSSLMODE=verify-full is required to reconcile live DB schema.")
    ca_path = source_env.get("PGSSLROOTCERT", "")
    if not ca_path or not Path(ca_path).is_file():
        raise ValueError("An existing PGSSLROOTCERT file is required to reconcile live DB schema.")
    cwd = Path(alembic_cwd)
    if not (cwd / "alembic.ini").is_file():
        raise ValueError(f"Alembic config not found in {cwd}.")
    attempts = max(1, retries)
    for attempt in range(1, attempts + 1):
        try:
            result = subprocess.run(
                [sys.executable, "-m", "alembic", "current"],
                cwd=str(cwd),
                env=source_env,
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                text=True,
                timeout=30,
                check=False,
            )
        except (subprocess.TimeoutExpired, OSError) as error:
            if attempt == attempts:
                raise ValueError("Live database Alembic revision query failed or timed out.") from error
            time.sleep(retry_delay)
            continue
        if result.returncode == 0:
            return parse_alembic_current(result.stdout)
        if attempt == attempts:
            raise ValueError("Live database Alembic revision query exited non-zero.")
        time.sleep(retry_delay)
    raise ValueError("Live database Alembic revision query did not succeed.")


def reconcile_live_production_base(
    repo: str,
    *,
    fly_app: str,
    alembic_cwd: str | Path = "anteroom",
    health_url: str = "",
    pin: recovery_evidence.EvidencePin | None = HISTORICAL_RECOVERY_CHECKPOINT,
    expected_fly_app: str = HISTORICAL_BASE_FLY_APP,
    expected_image_digest: str = HISTORICAL_BASE_IMAGE_DIGEST,
    expected_db_revision: str = HISTORICAL_BASE_DB_REVISION,
    required_groups: tuple[str, ...] = HISTORICAL_BASE_FLY_GROUPS,
    production_fn=None,
    git_fn=None,
    live_version_fn=None,
    fly_machines_fn=None,
    db_revision_fn=None,
) -> dict:
    """Fail closed before any deploy effect if recorded SHA, live web/worker Machines, or DB schema drifted."""
    if pin is None:
        raise ValueError("No recovery checkpoint is configured.")
    if repo != pin.repository:
        raise ValueError(f"Repository {repo!r} does not match pinned repository {pin.repository!r}.")
    if fly_app != expected_fly_app:
        raise ValueError(f"Fly app {fly_app!r} does not match pinned app {expected_fly_app!r}.")

    current_production = (production_fn or production_commit)(repo)
    if current_production != pin.production_base:
        raise ValueError(
            f"Recorded production commit {current_production!r} does not match pinned recovery base "
            f"{pin.production_base[:8]}."
        )

    run_git = git_fn or _read_git
    expected_version = _version_at(run_git, pin.production_base)
    if not expected_version:
        raise ValueError("Could not read expected VERSION for the pinned production base.")
    live_version = (live_version_fn or live_health_version)(health_url)
    if live_version != expected_version:
        raise ValueError(
            f"Live production version {live_version!r} does not match pinned base "
            f"{pin.production_base[:8]} version {expected_version!r}."
        )

    expected_label = f"v{expected_version}-{pin.production_base[:8]}"
    machines = (fly_machines_fn or live_fly_machines)(fly_app)
    checker = _load_fly_machine_checker()
    groups, wrong = checker.problems(
        machines,
        expected_label,
        required_groups=required_groups,
        expected_digest=expected_image_digest,
    )
    if wrong:
        raise ValueError("Live Fly Machines do not match proven recovery base: " + "; ".join(wrong))

    live_revision = (db_revision_fn or live_alembic_revision)(alembic_cwd)
    if live_revision != expected_db_revision:
        raise ValueError(
            f"Live database Alembic revision {live_revision!r} does not match proven recovery base "
            f"{expected_db_revision!r}; refusing to reuse recovery checkpoint."
        )

    return {
        "production_base": pin.production_base,
        "live_base_version": live_version,
        "fly_app": fly_app,
        "fly_label": expected_label,
        "fly_groups": groups,
        "fly_digest": expected_image_digest,
        "db_revision": live_revision,
    }


def _load_changed_components():
    spec = importlib.util.spec_from_file_location(
        "changed_components",
        Path(__file__).resolve().parent / "changed-components.py",
    )
    if spec is None or spec.loader is None:
        raise RuntimeError("Could not load changed-components.py.")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _parse_strict_json(raw: bytes):
    return json.loads(
        raw,
        object_pairs_hook=recovery_evidence.unique_object,
        parse_constant=lambda _: (_ for _ in ()).throw(ValueError("Non-finite JSON.")),
    )


def _normalize_pre_rename_path(path: str, *, pre_rename: bool) -> str:
    if not pre_rename:
        return path
    for old_prefix, new_prefix in PRE_RENAME_PREFIXES:
        if path.startswith(old_prefix):
            return new_prefix + path[len(old_prefix):]
    return path


def _without_version_for_commit(changed_mod, norm_path: str, text: str, *, pre_rename: bool) -> object:
    if pre_rename and norm_path.endswith("uv.lock"):
        text = re.sub(r'(?m)^(name = "vendoo-studio"\nversion = )"[^"]*"', r"\1", text)
    return changed_mod.without_version(norm_path, text)


def _is_version_only(changed_mod, run_git, before: str, tip: str, path: str, *, pre_rename: bool) -> bool:
    norm_path = _normalize_pre_rename_path(path, pre_rename=pre_rename)
    if norm_path in changed_mod.VERSION_ONLY:
        return True
    if norm_path not in changed_mod.VERSIONED:
        return False
    try:
        old_text = run_git("show", f"{before}:{path}")
        new_text = run_git("show", f"{tip}:{path}")
        return _without_version_for_commit(
            changed_mod, norm_path, old_text, pre_rename=pre_rename
        ) == _without_version_for_commit(
            changed_mod, norm_path, new_text, pre_rename=pre_rename
        )
    except (KeyError, ValueError, TypeError, AttributeError, RuntimeError, subprocess.SubprocessError, json.JSONDecodeError):
        return False


def _required_push_jobs(changed_mod, run_git, before: str, tip: str, local_files: list[str]) -> set[str]:
    top_level = set(run_git("ls-tree", "--name-only", tip).splitlines())
    pre_rename = "vendoo-studio" in top_level and "anteroom" not in top_level
    relevant_files = [
        _normalize_pre_rename_path(path, pre_rename=pre_rename)
        for path in local_files
        if not _is_version_only(changed_mod, run_git, before, tip, path, pre_rename=pre_rename)
    ]
    selected = changed_mod.components(relevant_files)
    if not isinstance(selected, dict) or set(selected) != set(CI_COMPONENT_JOBS):
        raise ValueError("Changed-component selector does not match the deploy gate CI job contract.")
    required = set(CI_ALWAYS_REQUIRED_JOBS)
    for component, needed in selected.items():
        if needed:
            required.update(CI_COMPONENT_JOBS[component])
    return required


def _validate_push_run(
    run: object,
    *,
    repo: str,
    tip: str,
    expected_tree: str,
    expected_run_id: int | None = None,
    expected_attempt: int | None = None,
    expected_check_suite_id: int | None = None,
) -> tuple[int, int, int, str, str]:
    if not isinstance(run, dict):
        raise ValueError("CI workflow run payload must be a JSON object.")
    run_id = run.get("id")
    attempt = run.get("run_attempt")
    check_suite_id = run.get("check_suite_id")
    if (
        type(run_id) is not int or run_id < 1
        or type(attempt) is not int or attempt < 1
        or type(check_suite_id) is not int or check_suite_id < 1
    ):
        raise ValueError("CI run must report positive integer run, attempt, and check suite IDs.")
    if expected_run_id is not None and run_id != expected_run_id:
        raise ValueError("CI run ID changed during verification.")
    if expected_attempt is not None and attempt != expected_attempt:
        raise ValueError("CI run attempt changed during verification.")
    if expected_check_suite_id is not None and check_suite_id != expected_check_suite_id:
        raise ValueError("CI check suite ID changed during verification.")
    repository = run.get("repository")
    head_repository = run.get("head_repository")
    head_commit = run.get("head_commit")
    if (
        not isinstance(repository, dict)
        or not isinstance(head_repository, dict)
        or not isinstance(head_commit, dict)
    ):
        raise ValueError("CI run is missing repository or head_commit provenance.")
    repo_id = repository.get("id")
    head_repo_id = head_repository.get("id")
    if (
        run.get("event") != "push"
        or run.get("head_branch") != "main"
        or run.get("head_sha") != tip
        or run.get("path") != CI_WORKFLOW_PATH
        or repository.get("full_name") != repo
        or head_repository.get("full_name") != repo
        or type(repo_id) is not int or repo_id < 1
        or head_repo_id != repo_id
        or head_commit.get("id") != tip
        or head_commit.get("tree_id") != expected_tree
    ):
        raise ValueError("CI push run provenance does not match the exact trusted repository, branch, workflow, and tree.")
    status = run.get("status")
    conclusion = run.get("conclusion")
    if not isinstance(status, str) or not status:
        raise ValueError("CI push run is missing a valid status.")
    if conclusion is not None and not isinstance(conclusion, str):
        raise ValueError("CI push run reported a non-string conclusion.")
    return run_id, attempt, check_suite_id, status, conclusion or ""


def _validate_check_suite(suite: object, *, check_suite_id: int, tip: str) -> str:
    if not isinstance(suite, dict):
        raise ValueError("Check suite payload must be a JSON object.")
    app = suite.get("app")
    if not isinstance(app, dict):
        raise ValueError("Check suite payload is missing app metadata.")
    before = suite.get("before")
    if (
        suite.get("id") != check_suite_id
        or app.get("slug") != "github-actions"
        or suite.get("head_branch") != "main"
        or suite.get("head_sha") != tip
        or suite.get("after") != tip
    ):
        raise ValueError("Check suite provenance does not match the GitHub Actions main push at tip.")
    verification_evidence.require_sha(before)
    if set(before) == {"0"} or before == tip:
        raise ValueError("Check suite before SHA is not a valid ancestor commit.")
    return before


def _validate_compare_diff(
    compare: object,
    *,
    before: str,
    tip: str,
    before_tree: str,
    tip_tree: str,
    push_span: list[str],
    local_files: list[str],
) -> None:
    if not isinstance(compare, dict):
        raise ValueError("GitHub compare payload must be a JSON object.")
    if (
        compare.get("status") != "ahead"
        or compare.get("behind_by") != 0
        or compare.get("ahead_by") != len(push_span)
        or compare.get("total_commits") != len(push_span)
    ):
        raise ValueError("GitHub compare commit counts do not match the exact local base..tip push span.")
    base_commit = compare.get("base_commit")
    merge_base = compare.get("merge_base_commit")
    commits = compare.get("commits")
    files = compare.get("files")
    if (
        not isinstance(base_commit, dict)
        or not isinstance(merge_base, dict)
        or not isinstance(commits, list)
        or not isinstance(files, list)
    ):
        raise ValueError("GitHub compare payload is missing commit or file evidence.")
    base_commit_obj = base_commit.get("commit")
    base_tree_obj = base_commit_obj.get("tree") if isinstance(base_commit_obj, dict) else None
    base_tree_sha = base_tree_obj.get("sha") if isinstance(base_tree_obj, dict) else None
    if (
        base_commit.get("sha") != before
        or merge_base.get("sha") != before
        or base_tree_sha != before_tree
    ):
        raise ValueError("GitHub compare base commit or tree does not match local Git.")
    if len(commits) != len(push_span):
        raise ValueError("GitHub compare commit list is truncated or mismatched.")
    if any(not isinstance(item, dict) for item in commits):
        raise ValueError("GitHub compare commit entry must be a JSON object.")
    if [item.get("sha") for item in commits] != push_span:
        raise ValueError("GitHub compare commit sequence does not match local main first-parent history.")
    last_commit_obj = commits[-1].get("commit")
    last_tree_obj = last_commit_obj.get("tree") if isinstance(last_commit_obj, dict) else None
    last_tree_sha = last_tree_obj.get("sha") if isinstance(last_tree_obj, dict) else None
    if last_tree_sha != tip_tree:
        raise ValueError("GitHub compare tip tree does not match local Git.")
    compare_files: set[str] = set()
    for entry in files:
        if not isinstance(entry, dict):
            raise ValueError("GitHub compare file entry must be a JSON object.")
        filename = entry.get("filename")
        if not isinstance(filename, str) or not filename:
            raise ValueError("GitHub compare file entry is missing a filename.")
        compare_files.add(filename)
        previous = entry.get("previous_filename")
        if previous is not None:
            if not isinstance(previous, str) or not previous:
                raise ValueError("GitHub compare file entry has an invalid previous_filename.")
            compare_files.add(previous)
    local_file_set = set(local_files)
    if len(files) < 300 or compare_files == local_file_set:
        if len(files) > 300 or compare_files != local_file_set:
            raise ValueError("GitHub compare changed file set does not match local base..tip diff.")
    else:
        if len(files) != 300 or not compare_files <= local_file_set:
            raise ValueError("GitHub compare truncated file set is inconsistent with local base..tip diff.")


class EvidenceReadError(RuntimeError):
    """A bounded API command could not supply evidence; retry on the next poll."""


def _compare_pages(fetch, before: str, tip: str, commit_count: int) -> dict:
    """Read ordered commit pages; GitHub supplies file evidence only on page 1."""
    combined = None
    commits = []
    for page in range(1, (commit_count + 99) // 100 + 1):
        data = fetch(f"compare/{before}...{tip}?per_page=100&page={page}")
        if not isinstance(data, dict) or not isinstance(data.get("commits"), list):
            raise ValueError("Invalid GitHub compare page.")
        if combined is None:
            combined = dict(data)
        elif any(data.get(key) != combined.get(key) for key in (
            "status", "behind_by", "ahead_by", "total_commits", "base_commit", "merge_base_commit"
        )):
            raise ValueError("GitHub compare metadata changed between pages.")
        if len(data["commits"]) != min(100, commit_count - len(commits)):
            raise ValueError("GitHub compare commit page is truncated or excessive.")
        commits.extend(data["commits"])
    combined["commits"] = commits
    return combined


def _validate_push_jobs(
    jobs: list[dict],
    *,
    run_id: int,
    attempt: int,
    tip: str,
    required_jobs: set[str],
) -> None:
    seen_names: set[str] = set()
    succeeded: set[str] = set()
    for job in jobs:
        if not isinstance(job, dict):
            raise ValueError("CI job entry must be a JSON object.")
        if (
            job.get("run_id") != run_id
            or job.get("run_attempt") != attempt
            or job.get("head_sha") != tip
        ):
            raise ValueError("CI job provenance does not match the verified push run attempt and tip commit.")
        name = job.get("name")
        if not isinstance(name, str) or not name or name in seen_names:
            raise ValueError("CI job inventory has a missing or duplicate job name.")
        seen_names.add(name)
        status = job.get("status")
        conclusion = job.get("conclusion")
        if status != "completed" or conclusion not in ("success", "skipped"):
            raise ValueError(f"CI job {name!r} did not succeed or cleanly skip (status={status!r}, conclusion={conclusion!r}).")
        if conclusion == "success":
            succeeded.add(name)
    missing_or_skipped = sorted(required_jobs - succeeded)
    if missing_or_skipped:
        raise ValueError(
            "Required CI checks for the base..tip diff were not exercised and passing: "
            + ", ".join(missing_or_skipped)
        )


def _evaluate_tip_push(
    repo: str,
    tip: str,
    full_span: list[str],
    production: str,
    *,
    fetch,
    run_git,
    changed_mod,
) -> dict:
    try:
        tip_tree = verification_evidence.require_sha(run_git("rev-parse", f"{tip}^{{tree}}"))
        runs_payload = fetch(f"actions/workflows/ci.yml/runs?head_sha={tip}&event=push&per_page=1")
        if not isinstance(runs_payload, dict) or not isinstance(runs_payload.get("workflow_runs"), list):
            raise ValueError("Invalid workflow_runs response for candidate push tip.")
        runs = runs_payload["workflow_runs"]
        if not runs:
            return {"status": "none", "reason": f"No push CI run on {tip[:8]}."}
        run_id, attempt, check_suite_id, run_status, run_conclusion = _validate_push_run(
            runs[0], repo=repo, tip=tip, expected_tree=tip_tree
        )
        suite = fetch(f"check-suites/{check_suite_id}")
        before = _validate_check_suite(suite, check_suite_id=check_suite_id, tip=tip)
        if before != production and before not in full_span:
            raise ValueError(f"Check suite base {before[:8]} is outside the production..HEAD deploy span.")
        before_tree = verification_evidence.require_sha(run_git("rev-parse", f"{before}^{{tree}}"))
        push_span = run_git("rev-list", "--first-parent", "--reverse", f"{before}..{tip}").splitlines()
        if not push_span or push_span[-1] != tip:
            raise ValueError(f"Local Git span {before[:8]}..{tip[:8]} is empty or does not end at {tip[:8]}.")
        if len(push_span) == 1:
            return {"status": "none", "reason": f"Push CI on {tip[:8]} tested only {tip[:8]}."}
        if run_git("rev-parse", f"{push_span[0]}^1") != before:
            raise ValueError(f"First-parent base of {push_span[0][:8]} does not match push base {before[:8]}.")
        if push_span[0] not in full_span:
            raise ValueError(f"Push span start {push_span[0][:8]} is outside production..HEAD.")
        start = full_span.index(push_span[0])
        if full_span[start : start + len(push_span)] != push_span:
            raise ValueError(f"Push span {before[:8]}..{tip[:8]} is not contiguous on main first-parent history.")
        local_raw = run_git("diff", "--no-renames", "--name-only", "-z", before, tip)
        local_files = sorted({path for path in local_raw.split("\0") if path})
        compare = _compare_pages(fetch, before, tip, len(push_span))
        _validate_compare_diff(
            compare,
            before=before,
            tip=tip,
            before_tree=before_tree,
            tip_tree=tip_tree,
            push_span=push_span,
            local_files=local_files,
        )
    except (KeyError, ValueError, TypeError, RuntimeError, OSError, subprocess.SubprocessError, json.JSONDecodeError) as error:
        return {"status": "invalid", "reason": f"Push coverage check at {tip[:8]} failed: {error}"}

    covered_intermediates = set(push_span[:-1])
    if run_status != "completed":
        return {
            "status": "waiting",
            "covered": covered_intermediates,
            "detail": f"push CI on {tip[:8]} (run {run_id}, covering {before[:8]}..{tip[:8]}) is {run_status}",
        }
    if run_conclusion != "success":
        return {
            "status": "failed",
            "covered": covered_intermediates,
            "detail": f"covering push CI on {tip[:8]} (run {run_id}, {before[:8]}..{tip[:8]}) finished as {run_conclusion!r}",
        }
    try:
        required_jobs = _required_push_jobs(changed_mod, run_git, before, tip, local_files)
        jobs = verification_evidence.all_jobs(fetch, str(run_id), str(attempt))
        _validate_push_jobs(jobs, run_id=run_id, attempt=attempt, tip=tip, required_jobs=required_jobs)
        # Re-read the run after reading attempt jobs to reject TOCTOU re-runs.
        latest_run = fetch(f"actions/runs/{run_id}")
        _, latest_attempt, _, latest_status, latest_conclusion = _validate_push_run(
            latest_run,
            repo=repo,
            tip=tip,
            expected_tree=tip_tree,
            expected_run_id=run_id,
            expected_check_suite_id=check_suite_id,
        )
        if latest_attempt > attempt:
            # Never reuse old jobs for a replacement attempt, even if it completed.
            # The next bounded poll reads and verifies that attempt's own inventory.
            return {
                "status": "waiting",
                "covered": covered_intermediates,
                "detail": f"push CI run {run_id} changed to attempt {latest_attempt}; awaiting fresh evidence",
            }
        if latest_attempt != attempt:
            raise ValueError("CI run attempt moved backwards during verification.")
        if latest_status != "completed" or latest_conclusion != "success":
            raise ValueError(
                f"Covering push run {run_id} changed state to status={latest_status!r}, conclusion={latest_conclusion!r}."
            )
    except EvidenceReadError as error:
        return {"status": "waiting", "covered": covered_intermediates, "detail": str(error)}
    except (KeyError, ValueError, TypeError, RuntimeError, OSError, subprocess.SubprocessError, json.JSONDecodeError) as error:
        return {
            "status": "failed",
            "covered": covered_intermediates,
            "detail": f"covering push CI on {tip[:8]} (run {run_id} attempt {attempt}, {before[:8]}..{tip[:8]}): {error}",
        }
    return {
        "status": "covered",
        "covered": covered_intermediates,
        "detail": (
            f"verified push CI on {tip[:8]} (run {run_id} attempt {attempt}, "
            f"{before[:8]}..{tip[:8]}, {len(required_jobs)} required checks passed)"
        ),
    }


def verify_push_coverage(
    repo: str,
    sha: str,
    all_checked: list[str],
    production: str | None,
    head_sha: str,
    *,
    fetch=None,
    git_fn=None,
    cache: dict[str, dict] | None = None,
) -> tuple[str, str]:
    """Fail-closed check whether a missing commit was tested by a verified multi-commit push at a later tip.

    Returns ``(state, detail)`` where ``state`` is one of:
    - ``"covered"``: exact trusted push/run, identical ``base..tip`` diff, and all required checks passed.
    - ``"waiting"``: exact verified covering push run at a later tip is still queued or running.
    - ``"failed"``: exact verified covering push run completed without all required checks passing.
    - ``"missing"``: no later tip proved coverage (caller must continue failing closed).
    """
    if not isinstance(repo, str) or not REPO_RE.fullmatch(repo):
        return "missing", f"Invalid repository {repo!r}."
    if production is None:
        return "missing", "Recorded production commit is unknown."
    try:
        verification_evidence.require_sha(sha)
        verification_evidence.require_sha(production)
        verification_evidence.require_sha(head_sha)
    except ValueError as error:
        return "missing", str(error)
    if sha not in all_checked:
        return "missing", f"Commit {sha[:8]} is not in the deploy span."
    candidates = all_checked[all_checked.index(sha) + 1 :]
    if not candidates:
        return "missing", f"No later commit after {sha[:8]} in the deploy span."

    run_git = git_fn or _read_git
    try:
        full_span = run_git("rev-list", "--first-parent", "--reverse", f"{production}..{head_sha}").splitlines()
        if not full_span or full_span[-1] != head_sha or sha not in full_span:
            return "missing", f"Commit {sha[:8]} is not on first-parent history {production[:8]}..{head_sha[:8]}."
        changed_mod = _load_changed_components()
    except (KeyError, ValueError, RuntimeError, OSError, subprocess.SubprocessError) as error:
        return "missing", f"Could not inspect local deploy span: {error}"

    def default_fetch(path: str):
        limit = MAX_COMPARE_BYTES if path.startswith("compare/") else recovery_evidence.MAX_ARCHIVE
        try:
            raw = recovery_evidence.read_command(
                ["gh", "api", "--method", "GET", f"repos/{repo}/{path}"], limit=limit
            )
        except (ValueError, OSError, subprocess.SubprocessError) as error:
            raise EvidenceReadError("Could not read push CI evidence; awaiting next poll.") from error
        return _parse_strict_json(raw)

    api_fetch = fetch or default_fetch
    tip_cache = {} if cache is None else cache
    last_reason = f"No verified multi-commit push covered {sha[:8]}."
    for tip in candidates:
        if tip not in tip_cache:
            tip_cache[tip] = _evaluate_tip_push(
                repo,
                tip,
                full_span,
                production,
                fetch=api_fetch,
                run_git=run_git,
                changed_mod=changed_mod,
            )
        result = tip_cache[tip]
        covered = result.get("covered", ())
        if sha in covered:
            return result["status"], result["detail"]
        if result.get("status") == "invalid" and result.get("reason"):
            last_reason = result["reason"]
    return "missing", last_reason


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repo", required=True)
    parser.add_argument("--head", default="HEAD")
    parser.add_argument("--timeout", type=int, default=40 * 60)
    parser.add_argument("--interval", type=int, default=30)
    parser.add_argument("--health-url", default=os.environ.get("HEALTH_URL", ""))
    parser.add_argument("--reconcile-live-base", action="store_true")
    parser.add_argument("--fly-app", default=os.environ.get("FLY_APP", ""))
    parser.add_argument("--alembic-cwd", default="anteroom")
    args = parser.parse_args()

    if args.reconcile_live_base:
        try:
            summary = reconcile_live_production_base(
                args.repo,
                fly_app=args.fly_app,
                alembic_cwd=args.alembic_cwd,
                health_url=args.health_url,
                pin=HISTORICAL_RECOVERY_CHECKPOINT,
            )
        except (ValueError, RuntimeError, OSError, subprocess.SubprocessError, json.JSONDecodeError) as error:
            print(f"::error::Live recovery base reconciliation failed: {error}")
            return 1
        groups_text = ", ".join(f"{group}={count}" for group, count in sorted(summary["fly_groups"].items()))
        print(
            f"Reconciled live recovery base {summary['production_base'][:8]}: "
            f"health={summary['live_base_version']}, Fly {summary['fly_app']} ({groups_text}) "
            f"on {summary['fly_label']}, DB Alembic revision={summary['db_revision']}."
        )
        return 0

    head_sha = git("rev-parse", args.head)
    production = production_commit(args.repo)
    all_checked, why = commits_to_check(head_sha, production)
    pending = list(all_checked)
    print(why)
    deadline = time.monotonic() + args.timeout
    missing_since: dict[str, float] = {}
    superseded_historical: list[str] = []
    covered_commits: set[str] = set()
    while True:
        waiting = []
        coverage_cache: dict[str, dict] = {}
        for sha in pending:
            status, conclusion = ci_state(args.repo, sha)
            if status == "completed":
                if conclusion == "success":
                    print(f"CI passed on {sha[:8]}.")
                    continue
                eligible, reason = False, ""
                if conclusion == "failure":
                    try:
                        eligible, reason = checkpoint_eligible(
                            args.repo, head_sha, production, sha, pin=HISTORICAL_RECOVERY_CHECKPOINT
                        )
                    except (ValueError, subprocess.SubprocessError) as error:
                        reason = str(error)
                else:
                    reason = f"Expected historical failure conclusion 'failure', got {conclusion!r}."
                if eligible:
                    superseded_historical.append(sha)
                    print(
                        f"CI on {sha[:8]} finished as '{conclusion}'; deferring to live verification "
                        f"of pinned Descendant verification checkpoint {HISTORICAL_RECOVERY_CHECKPOINT.target[:8]} "
                        "once every other commit in the deploy span passes CI."
                    )
                    continue
                detail = f" Recovery checkpoint ineligible: {reason}" if sha in HISTORICAL_RECOVERY_FAILURES and reason else ""
                print(
                    f"::error::CI on {sha[:8]} finished as '{conclusion}', and this deploy ships its code. "
                    f"Re-run CI for a transient failure. For a regression fixed on main, see the hosted recovery "
                    f"procedure in docs/ci.md.{detail}"
                )
                return 1
            if status == "missing":
                cov_state, cov_detail = verify_push_coverage(
                    args.repo,
                    sha,
                    all_checked,
                    production,
                    head_sha,
                    cache=coverage_cache,
                )
                if cov_state == "covered":
                    covered_commits.add(sha)
                    missing_since.pop(sha, None)
                    print(f"CI on {sha[:8]} is covered by {cov_detail}.")
                    continue
                if cov_state == "waiting":
                    missing_since.pop(sha, None)
                    waiting.append((sha, f"waiting ({cov_detail})"))
                    continue
                if cov_state == "failed":
                    print(
                        f"::error::CI on {sha[:8]} failed closed ({cov_detail}), and this deploy ships its code. "
                        "Re-run the covering CI run or fix main; its passing run deploys."
                    )
                    return 1
                since = missing_since.setdefault(sha, time.monotonic())
                # CI registers its check within seconds of a push; a long absence means it never ran.
                if time.monotonic() - since > 10 * 60:
                    print(
                        f"::error::No CI check on {sha[:8]}. Its CI never ran, so this code is untested. "
                        f"({cov_detail})"
                    )
                    return 1
            else:
                missing_since.pop(sha, None)
            waiting.append((sha, status))
        if not waiting and covered_commits:
            # Discard all earlier evidence before authorizing success. This also
            # catches reruns started while later direct checks were being read.
            final_cache: dict[str, dict] = {}
            for covered_sha in all_checked:
                if covered_sha not in covered_commits:
                    continue
                state, detail = verify_push_coverage(
                    args.repo, covered_sha, all_checked, production, head_sha, cache=final_cache
                )
                if state == "failed":
                    print(f"::error::Final push coverage verification failed: {detail}")
                    return 1
                if state != "covered":
                    waiting.append((covered_sha, f"awaiting fresh coverage ({detail})"))
        if not waiting:
            if not superseded_historical:
                _write_github_output("recovery_checkpoint", "false")
                return 0
            try:
                current_production = production_commit(args.repo)
                if current_production != production:
                    raise ValueError(
                        "Recorded production commit changed while waiting for CI; aborting recovery checkpoint."
                    )
                report = verify_historical_checkpoint(
                    args.repo,
                    head_sha,
                    current_production,
                    superseded_historical[0],
                    pin=HISTORICAL_RECOVERY_CHECKPOINT,
                    health_url=args.health_url,
                )
            except (ValueError, RuntimeError, OSError, subprocess.SubprocessError, json.JSONDecodeError) as error:
                print(f"::error::Recovery checkpoint rejected: {error}")
                return 1
            _write_github_output("recovery_checkpoint", "true")
            commits = ", ".join(sha[:8] for sha in superseded_historical)
            print(
                f"Historical CI failures on {commits} are superseded by verified Descendant "
                f"verification checkpoint {report['target'][:8]} (run {report['run_id']} attempt {report['attempt']}, "
                f"receipt artifact {report['receipt_artifact_id']}; live base {report['production_base'][:8]} "
                f"confirmed at {report['live_base_version']}). Every other commit through {head_sha[:8]} passed push CI."
            )
            return 0
        # Recheck accepted coverage on every poll, including the final poll.
        # Each fresh evaluation verifies the current covering run and attempt.
        pending = [sha for sha in all_checked if sha in covered_commits or any(sha == item[0] for item in waiting)]
        if time.monotonic() > deadline:
            print(f"::error::CI on {', '.join(s[:8] for s in pending)} did not finish within {args.timeout // 60} minutes.")
            return 1
        for sha, status in waiting:
            print(f"CI on {sha[:8]} is {status}; waiting.")
        time.sleep(args.interval)


if __name__ == "__main__":
    sys.exit(main())
