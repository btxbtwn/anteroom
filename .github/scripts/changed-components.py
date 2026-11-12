#!/usr/bin/env python3
"""Say which components a change touches, so CI runs only their jobs.

Prints one ``name=true`` or ``name=false`` line per check group
in the format of ``$GITHUB_OUTPUT``. With no base commit, or one git cannot
find, every component counts as changed.

A file whose only difference is the release version counts as unchanged. The
Version workflow's "Release Anteroom" commits change nothing else, so they
skip the test jobs, and the CI aggregator still reports success for the deploy
that waits on it (.github/workflows/deploy-hosted.yml).

    changed-components.py --base <sha> [--head <sha>]
    git diff --name-only | changed-components.py --stdin    (check.sh)
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys

COMPONENTS = ("frontend", "backend", "browser", "health", "audit", "extension", "relay")

# Files every job reads: a change here runs everything.
CI_FILES = (".github/workflows/ci.yml", ".github/workflows/dependency-audit.yml", ".github/scripts/", "check.sh")

# Changes that no test job reads. Release notes and the changelog are checked
# by the Changelog job, which always runs.
# Read by backend tests although no Studio code uses them (test_xcodegen_installer.py).
IOS_READ_BY_BACKEND = ("anteroom-ios/tools/", "anteroom-ios/ci_scripts/")
NOT_STUDIO = (
    "webhook-relay/",
    "anteroom-ios/",
    "docs/",
    "changelog.d/",
)
# Studio's contract check regenerates these from the backend, so it must run
# when they change (contracts/ is not in NOT_STUDIO, so it already counts).
STUDIO_GENERATED = ("anteroom-ios/Sources/API/Generated/",)

# Files the Version workflow rewrites with nothing but the new version.
VERSION_ONLY = {"CHANGELOG.md", "vendoo-studio/VERSION"}
VERSIONED = {
    "vendoo-studio/package.json",
    "vendoo-studio/package-lock.json",
    "vendoo-studio/pyproject.toml",
    "vendoo-studio/uv.lock",
}


def git(*args: str) -> str:
    return subprocess.run(["git", *args], check=True, capture_output=True, text=True).stdout


def show(commit: str, path: str) -> str | None:
    result = subprocess.run(["git", "show", f"{commit}:{path}"], capture_output=True, text=True)
    return result.stdout if result.returncode == 0 else None


def without_version(path: str, text: str) -> object:
    if path.endswith(".json"):
        data = json.loads(text)
        data.pop("version", None)
        root = data.get("packages", {}).get("")
        if isinstance(root, dict):
            root.pop("version", None)
        return data
    if path.endswith("uv.lock"):
        return re.sub(r'(?m)^(name = "vendoo-studio"\nversion = )"[^"]*"', r"\1", text)
    return re.sub(r'(?m)^version\s*=\s*"[^"]*"', "", text, count=1)


def version_only(base: str, head: str, path: str) -> bool:
    if path in VERSION_ONLY:
        return True
    if path not in VERSIONED:
        return False
    before, after = show(base, path), show(head, path)
    if before is None or after is None:
        return False
    return without_version(path, before) == without_version(path, after)


def components(files: list[str]) -> dict[str, bool]:
    selected: set[str] = set()
    for path in files:
        if path.startswith(CI_FILES):
            selected.update(COMPONENTS)
        elif path.startswith("webhook-relay/"):
            selected.add("relay")
        elif path.startswith(STUDIO_GENERATED) or path.startswith("contracts/") or path.startswith(IOS_READ_BY_BACKEND):
            selected.update(("backend", "health"))
        elif path.startswith(NOT_STUDIO) or ("/" not in path and path.endswith(".md")):
            continue
        elif path in {"vendoo-studio/pyproject.toml", "vendoo-studio/uv.lock", "vendoo-studio/scripts/install-locked.sh"}:
            selected.update(("backend", "browser", "health", "audit"))
        elif path in {"vendoo-studio/package.json", "vendoo-studio/package-lock.json"}:
            # The extension linter comes from Studio's npm dependencies.
            selected.update(("frontend", "backend", "browser", "health", "extension"))
        elif path.startswith("vendoo-studio/server/tests/"):
            selected.update(("backend", "health"))
        elif path.startswith("vendoo-studio/server/"):
            selected.update(("backend", "browser", "health"))
        elif path.startswith(("vendoo-studio/src/", "vendoo-studio/public/")):
            # Backend tests inspect frontend source; retain both suites.
            selected.update(("frontend", "backend", "health"))
            if not path.endswith((".test.ts", ".test.tsx", ".spec.ts", ".spec.tsx")):
                selected.add("browser")
        elif path.startswith(("vendoo-studio/e2e/", "vendoo-studio/hosted-e2e/", "vendoo-studio/playwright")):
            # knip also scans e2e imports as part of the frontend job.
            selected.update(("frontend", "backend", "browser", "health"))
        elif path.startswith("vendoo-extension/"):
            selected.update(("extension", "backend", "browser", "health"))
            if path == "vendoo-extension/manifest.json":
                # The web app shows the extension's version from it.
                selected.add("frontend")
        elif path.startswith("skills/"):
            selected.update(("backend", "browser", "health"))
            if path.startswith("skills/list-this/references/"):
                # The editor imports the field list, and the extension's
                # value matrix reads the marketplace option lists.
                selected.update(("frontend", "extension"))
        else:
            # Unknown inputs run every Studio suite rather than silently
            # dropping coverage. Audits depend on the locked Python packages.
            selected.update(("frontend", "backend", "browser", "health"))
    return {name: name in selected for name in COMPONENTS}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    parser.add_argument("--base", default="")
    parser.add_argument("--head", default="HEAD")
    parser.add_argument("--stdin", action="store_true", help="read changed paths from stdin")
    args = parser.parse_args()

    if args.stdin:
        result = components([line.strip() for line in sys.stdin if line.strip()])
        for name in COMPONENTS:
            print(f"{name}={'true' if result[name] else 'false'}")
        return 0

    base = args.base.strip()
    known = bool(base) and set(base) != {"0"} and subprocess.run(
        ["git", "cat-file", "-e", f"{base}^{{commit}}"], capture_output=True
    ).returncode == 0
    if not known:
        print(f"No usable base commit ({base or 'none'}); running every component.", file=sys.stderr)
        result = dict.fromkeys(COMPONENTS, True)
    else:
        # A rename must count both the removed and added path, including when
        # it moves a file between components.
        changed = [path for path in git("diff", "--no-renames", "--name-only", base, args.head).splitlines() if path]
        files = [path for path in changed if not version_only(base, args.head, path)]
        print(f"{len(changed)} changed file(s), {len(files)} beyond the version:", file=sys.stderr)
        for path in files:
            print(f"  {path}", file=sys.stderr)
        result = components(files)
    for name in COMPONENTS:
        print(f"{name}={'true' if result[name] else 'false'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
