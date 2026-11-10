#!/usr/bin/env python3
"""Wait until CI has passed on the code a deploy is about to ship.

A "Release Anteroom" commit from the Version workflow only changes version
numbers, and its CI skips the component checks. The code it ships was checked
by CI on the merge just before it, which can still be running when the release
commit's CI passes. This walks back over release commits to that merge and
waits for its CI run. Non-release commits must pass the same check, including
manual deployments that have no successful workflow_run event. It exits 1 if that check fails, is
cancelled, never appears or does not finish in time.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time

RELEASE_PREFIX = "Release Anteroom "


def git(*args: str) -> str:
    return subprocess.run(["git", *args], check=True, capture_output=True, text=True).stdout.strip()


def code_commit(head: str) -> str:
    """The nearest first-parent commit at or before head that is not a release commit."""
    sha = git("rev-parse", head)
    while git("log", "-1", "--format=%s", sha).startswith(RELEASE_PREFIX):
        sha = git("rev-parse", f"{sha}^")
    return sha


def ci_state(repo: str, sha: str) -> tuple[str, str]:
    """(status, conclusion) of the latest CI run for a push of sha, or ("missing", "")."""
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

    head = git("rev-parse", args.head)
    sha = code_commit(head)
    if sha == head:
        print(f"Checking CI on code commit {sha[:8]}, including manual deployments.")
    else:
        print(f"{head[:8]} is a release commit; checking CI on the code it ships, {sha[:8]}.")
    deadline = time.monotonic() + args.timeout
    missing_since = None
    while True:
        status, conclusion = ci_state(args.repo, sha)
        if status == "completed":
            if conclusion == "success":
                print(f"CI passed on {sha[:8]}.")
                return 0
            print(
                f"::error::CI on {sha[:8]} finished as '{conclusion}'. Re-run it if it was cancelled, "
                "or fix main; then run Deploy hosted again."
            )
            return 1
        if status == "missing":
            missing_since = missing_since or time.monotonic()
            # CI registers its check within seconds of a push; a long absence means it never ran.
            if time.monotonic() - missing_since > 10 * 60:
                print(f"::error::No CI check on {sha[:8]}. Its CI never ran, so this code is untested.")
                return 1
        else:
            missing_since = None
        if time.monotonic() > deadline:
            print(f"::error::CI on {sha[:8]} did not finish within {args.timeout // 60} minutes.")
            return 1
        print(f"CI on {sha[:8]} is {status}; waiting.")
        time.sleep(args.interval)


if __name__ == "__main__":
    sys.exit(main())
