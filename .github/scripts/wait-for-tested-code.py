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
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time

RELEASE_PREFIX = "Release Anteroom "
DEPLOYED_TITLE = "Deployed commit"


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


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repo", required=True)
    parser.add_argument("--head", default="HEAD")
    parser.add_argument("--timeout", type=int, default=40 * 60)
    parser.add_argument("--interval", type=int, default=30)
    args = parser.parse_args()

    pending, why = commits_to_check(args.head, production_commit(args.repo))
    print(why)
    deadline = time.monotonic() + args.timeout
    missing_since: dict[str, float] = {}
    while True:
        waiting = []
        for sha in pending:
            status, conclusion = ci_state(args.repo, sha)
            if status == "completed":
                if conclusion == "success":
                    print(f"CI passed on {sha[:8]}.")
                    continue
                print(
                    f"::error::CI on {sha[:8]} finished as '{conclusion}', and this deploy ships its code. "
                    "Re-run it if it failed for an unrelated reason, or fix main; its passing run deploys."
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
