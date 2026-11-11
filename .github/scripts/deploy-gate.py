#!/usr/bin/env python3
"""Decide whether Deploy hosted ships the main it checked out.

The deploy checks out main's tip, never the commit whose CI started it: CI runs
can finish out of order, and a queued deploy can replace another, so shipping
the triggering commit could put older code over newer code (and older code on
a database already migrated past it). It then skips, successfully, when:

* changelog.d/ has pending notes: the Version workflow's "Release Anteroom"
  commit follows and deploys instead;
* the tip is not the commit whose CI started this run and CI on the tip has
  not passed yet: the tip's own CI starts the deploy that ships it.

Writes ``skip`` and ``version`` to $GITHUB_OUTPUT. Waiting for CI on the merge
a release commit ships is wait-for-tested-code.py's job.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path


def git(*args: str) -> str:
    return subprocess.run(["git", *args], check=True, capture_output=True, text=True).stdout.strip()


def pending_notes(root: Path) -> bool:
    notes = root / "changelog.d"
    return notes.is_dir() and any(path.is_file() and path.name != "README.md" for path in notes.iterdir())


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


def decide(head: str, trigger: str, notes: bool, state) -> tuple[bool, str]:
    """(skip, message). ``state`` is called for CI on head only when needed."""
    if notes:
        return True, "Pending changelog notes; the release commit will deploy."
    if head == trigger:
        return False, f"Deploying {head[:8]}, main's tip, whose CI started this run."
    status, conclusion = state(head)
    if status == "completed" and conclusion == "success":
        return False, f"main moved to {head[:8]} after {trigger[:8]}; CI passed on it, so deploying {head[:8]}."
    if status == "completed":
        return True, (
            f"main's tip {head[:8]} finished CI as '{conclusion}'; not deploying {trigger[:8]} over it. "
            "Fix main to deploy again."
        )
    return True, f"main moved to {head[:8]} after {trigger[:8]}; its CI ({status}) starts the deploy that ships it."


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repo", required=True)
    parser.add_argument("--trigger", required=True, help="The commit whose CI (or dispatch) started this run.")
    args = parser.parse_args()

    head = git("rev-parse", "HEAD")
    root = Path(git("rev-parse", "--show-toplevel"))
    skip, message = decide(head, git("rev-parse", args.trigger), pending_notes(root),
                           lambda sha: ci_state(args.repo, sha))
    print(f"::notice::{message}" if skip else message)
    lines = [f"skip={'true' if skip else 'false'}"]
    if not skip:
        lines.append(f"version={(root / 'vendoo-studio' / 'VERSION').read_text().strip()}")
    output = os.environ.get("GITHUB_OUTPUT")
    if output:
        with open(output, "a", encoding="utf-8") as handle:
            handle.write("\n".join(lines) + "\n")
    else:
        print("\n".join(lines))
    return 0


if __name__ == "__main__":
    sys.exit(main())
