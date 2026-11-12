#!/usr/bin/env python3
"""Choose the commit Deploy hosted ships, and check it out.

CI runs on main finish out of order, and a queued deploy can replace another,
so shipping the commit whose CI started this run could put older code over
newer code (and on a database already migrated past it). Waiting for main's
tip instead starves deploys while merges keep landing faster than CI.

So the deploy ships the newest commit on main's first-parent line, from the
triggering commit up to the tip, that

* has no pending changelog.d/ notes (the Version workflow's "Release Anteroom"
  commit after it ships that code with its version), and
* passed CI. The trigger is checked too: a manual run starts from main's tip,
  whose CI nothing has confirmed.

Every later run considers at least the commits this one did, so production
never moves backwards. A newer commit whose CI is still running gets its own
run when that CI finishes. Writes ``skip`` and ``version`` to $GITHUB_OUTPUT.
Checking CI on every merge since the commit production runs is
wait-for-tested-code.py's job.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from collections.abc import Callable, Iterable
from pathlib import Path

NOTES_DIR = "changelog.d"


def git(*args: str) -> str:
    return subprocess.run(["git", *args], check=True, capture_output=True, text=True).stdout.strip()


def has_pending_notes(sha: str) -> bool:
    names = git("ls-tree", "--name-only", sha, f"{NOTES_DIR}/").splitlines()
    return any(Path(name).name != "README.md" for name in names)


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


def candidates(trigger: str, limit: int = 200) -> list[str]:
    """main's first-parent commits from the tip back to the trigger, newest first."""
    found: list[str] = []
    for sha in git("rev-list", "--first-parent", f"--max-count={limit}", "HEAD").splitlines():
        found.append(sha)
        if sha == trigger:
            return found
    # The trigger is not on main's first-parent line (a re-run of an old
    # commit, or a manual run): consider only the tip.
    return found[:1]


def choose(
    commits: Iterable[str],
    notes: Callable[[str], bool],
    state: Callable[[str], tuple[str, str]],
) -> tuple[str | None, list[str]]:
    """(the commit to ship or None, why each newer commit was passed over)."""
    passed: list[str] = []
    for sha in commits:
        if notes(sha):
            passed.append(f"{sha[:8]} has pending changelog notes; its release commit ships it.")
            continue
        status, conclusion = state(sha)
        if status == "completed" and conclusion == "success":
            return sha, passed
        shown = conclusion if status == "completed" else status
        passed.append(f"{sha[:8]}: CI {shown}; its own CI run deploys it once it passes.")
    return None, passed


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repo", required=True)
    parser.add_argument("--trigger", required=True, help="The commit whose CI (or dispatch) started this run.")
    args = parser.parse_args()

    trigger = git("rev-parse", args.trigger)
    target, passed = choose(candidates(trigger), has_pending_notes, lambda sha: ci_state(args.repo, sha))
    for line in passed:
        print(line)
    lines = [f"skip={'false' if target else 'true'}"]
    if target:
        if target != git("rev-parse", "HEAD"):
            git("checkout", "--quiet", "--detach", target)
        root = Path(git("rev-parse", "--show-toplevel"))
        lines.append(f"version={(root / 'vendoo-studio' / 'VERSION').read_text().strip()}")
        print(f"Deploying {target[:8]}, the newest tested commit on main from {trigger[:8]} on.")
    else:
        print(f"::notice::Nothing to deploy from {trigger[:8]} on; a newer commit's own run ships it.")
    output = os.environ.get("GITHUB_OUTPUT")
    if output:
        with open(output, "a", encoding="utf-8") as handle:
            handle.write("\n".join(lines) + "\n")
    else:
        print("\n".join(lines))
    return 0


if __name__ == "__main__":
    sys.exit(main())
