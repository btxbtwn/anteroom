#!/usr/bin/env python3
"""Wait until CI has passed on every merge a deploy is about to ship.

CI on main picks its jobs from the change against the previous commit, so a
docs-only merge can pass while an earlier merge's code failed. Shipping the
newer one ships both. So every first-parent commit on main after the one
production runs, up to the one being shipped, must have passed CI. "Release
Anteroom" commits from the Version workflow only change version numbers and
their CI skips the component checks, so they are left out; the merge before
one can still be running when the release commit's CI passes, so this waits.

The commit production runs is the one the last successful Deploy hosted run
recorded with a "Deployed commit" notice. Without one, only the nearest merge
is checked. It exits 1 if a check fails, is cancelled, never appears or does
not finish in time.

When recorded and live production are still on the pinned pre-recovery base
(b73c5347, 0.1.1011), the single historical Postgres failure on fa824f9a (fixed
in #1653) may be superseded only after all other commits in the span pass push
CI and the exact Descendant verification receipt for b28ba4b3 (run 44662061597,
attempt 1, artifact 14596571781) is live-verified against GitHub and local Git.
Once Deploy hosted succeeds and records a newer Deployed commit, the checkpoint
never matches again and ordinary gating applies unchanged.
"""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
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

HISTORICAL_RECOVERY_CHECKPOINT = recovery_evidence.EvidencePin(
    repository="btxbtwn/anteroom",
    repository_id=1183824317,
    workflow_id=422931456,
    target="b28ba4b361071373731b93bc49c12b4bf78326e9",
    tree="07ff8975695a4182c00c2605b5679d56e493c67b",
    production_base="b73c534707b9ef9bd70e4dc589b9c4b10863a852",
    workflow_blob="a0a097906554252a26f35c957169ec6d0e3c93db",
    ci_blob="3ee3a2df9cda1261e08aa55a2ee639ab9075c380",
    run_id=44662061597,
    attempt=1,
    receipt_artifact_id=14596571781,
    receipt_digest="sha256:4d0f38c8b8125c78810c0eecfb9301e74bd73a8e3f2df148cb38879c328ed791",
)


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
    except (urllib.error.URLError, TimeoutError, OSError) as error:
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
    if failed_sha != verification_evidence.HISTORICAL:
        return False, f"Commit {failed_sha[:8]} is not the diagnosed historical failure."
    run_git = git_fn or _read_git
    resolved_head = run_git("rev-parse", head)
    main_line = run_git("rev-list", "--first-parent", "origin/main").splitlines()
    if resolved_head not in main_line:
        return False, f"Target {resolved_head[:8]} is not on origin/main first-parent history."
    span = run_git("rev-list", "--first-parent", "--reverse", f"{production}..{resolved_head}").splitlines()
    if failed_sha not in span or verification_evidence.CORRECTED not in span or pin.target not in span:
        return False, "Historical failure, correcting merge, and verified checkpoint must all precede HEAD after production."
    if not (span.index(failed_sha) < span.index(verification_evidence.CORRECTED) <= span.index(pin.target)):
        return False, "First-parent ordering must be production < historical failure < correcting merge <= checkpoint <= HEAD."
    return True, ""


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

    expected_version = run_git("show", f"{pin.production_base}:vendoo-studio/VERSION").strip()
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


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repo", required=True)
    parser.add_argument("--head", default="HEAD")
    parser.add_argument("--timeout", type=int, default=40 * 60)
    parser.add_argument("--interval", type=int, default=30)
    parser.add_argument("--health-url", default=os.environ.get("HEALTH_URL", ""))
    args = parser.parse_args()

    head_sha = git("rev-parse", args.head)
    production = production_commit(args.repo)
    pending, why = commits_to_check(head_sha, production)
    print(why)
    deadline = time.monotonic() + args.timeout
    missing_since: dict[str, float] = {}
    superseded_historical: str | None = None
    while True:
        waiting = []
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
                if eligible and superseded_historical is None:
                    superseded_historical = sha
                    print(
                        f"CI on {sha[:8]} finished as '{conclusion}'; deferring to live verification "
                        f"of pinned Descendant verification checkpoint {HISTORICAL_RECOVERY_CHECKPOINT.target[:8]} "
                        "once every other commit in the deploy span passes CI."
                    )
                    continue
                detail = f" Recovery checkpoint ineligible: {reason}" if sha == verification_evidence.HISTORICAL and reason else ""
                print(
                    f"::error::CI on {sha[:8]} finished as '{conclusion}', and this deploy ships its code. "
                    f"Re-run it if it failed for an unrelated reason, or fix main; its passing run deploys.{detail}"
                )
                return 1
            if status == "missing":
                since = missing_since.setdefault(sha, time.monotonic())
                # CI registers its check within seconds of a push; a long absence means it never ran.
                if time.monotonic() - since > 10 * 60:
                    print(f"::error::No CI check on {sha[:8]}. Its CI never ran, so this code is untested.")
                    return 1
            else:
                missing_since.pop(sha, None)
            waiting.append((sha, status))
        if not waiting:
            if superseded_historical is None:
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
                    superseded_historical,
                    pin=HISTORICAL_RECOVERY_CHECKPOINT,
                    health_url=args.health_url,
                )
            except (ValueError, RuntimeError, OSError, subprocess.SubprocessError, json.JSONDecodeError) as error:
                print(f"::error::Recovery checkpoint rejected for {superseded_historical[:8]}: {error}")
                return 1
            print(
                f"Historical CI failure on {superseded_historical[:8]} is superseded by verified Descendant "
                f"verification checkpoint {report['target'][:8]} (run {report['run_id']} attempt {report['attempt']}, "
                f"receipt artifact {report['receipt_artifact_id']}; live base {report['production_base'][:8]} "
                f"confirmed at {report['live_base_version']}). Every other commit through {head_sha[:8]} passed push CI."
            )
            return 0
        pending = [sha for sha, _ in waiting]
        if time.monotonic() > deadline:
            print(f"::error::CI on {', '.join(s[:8] for s in pending)} did not finish within {args.timeout // 60} minutes.")
            return 1
        for sha, status in waiting:
            print(f"CI on {sha[:8]} is {status}; waiting.")
        time.sleep(args.interval)


if __name__ == "__main__":
    sys.exit(main())
