#!/usr/bin/env python3
"""Fail on Python functions, methods, classes and properties nothing calls.

    python scripts/check-dead-code.py                  # check
    python scripts/check-dead-code.py --write-baseline # rewrite the baseline

Runs vulture over the backend, counting the tests as callers. Unused variables
and attributes are left out: vulture reports every Pydantic field and database
column as one, so they would fail every new field.

server/dead_code_baseline.py lists what was already unused when the check was
added. Remove an entry when you delete that code. Something that is called
only by name (a callback, a handler method Python's library calls) goes in
server/dead_code_allowlist.py with a comment saying who calls it.
"""

from __future__ import annotations

import argparse
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BASELINE = ROOT / "server" / "dead_code_baseline.py"
ALLOWLIST = ROOT / "server" / "dead_code_allowlist.py"
KINDS = ("function", "method", "class", "property")
FINDING = re.compile(r"^(?P<path>[^:]+):(?P<line>\d+): unused (?P<kind>\w+) '(?P<name>[^']+)'")


def vulture(*extra: str) -> list[str]:
    command = [
        sys.executable, "-m", "vulture",
        "server/vendoo_studio", "server/tests", str(ALLOWLIST.relative_to(ROOT)), *extra,
        "--exclude", "server/vendoo_studio/migrations/",
        "--ignore-decorators", "@*router.*,@app.*,@*.middleware,@event.*,@*.listens_for,@field_validator,@model_validator,@pytest.*",
        "--min-confidence", "60",
    ]
    result = subprocess.run(command, cwd=ROOT, capture_output=True, text=True)
    if result.returncode not in (0, 3):
        sys.stderr.write(result.stderr)
        raise SystemExit(result.returncode)
    return result.stdout.splitlines()


def findings(lines: list[str]) -> list[str]:
    kept = []
    for line in lines:
        match = FINDING.match(line)
        if match and match["kind"] in KINDS and match["path"].startswith("server/vendoo_studio/"):
            kept.append(line)
    return kept


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--write-baseline", action="store_true")
    args = parser.parse_args()

    if args.write_baseline:
        BASELINE.write_text("")
        whitelist = vulture("--make-whitelist")
        names = {FINDING.match(line)["name"] for line in findings(vulture())}
        entries = [line for line in whitelist if (m := re.match(r"^(?:_\.)?(\w+)", line)) and m.group(1) in names]
        BASELINE.write_text(
            '"""Unused code that existed when the dead-code check was added. Shrink it; never add to it."""\n'
            "# ruff: noqa\n# fmt: off\n" + "\n".join(entries) + "\n"
        )
        print(f"Wrote {len(entries)} entries to {BASELINE.relative_to(ROOT)}")
        return 0

    unused = findings(vulture(str(BASELINE.relative_to(ROOT))))
    for line in unused:
        print(line)
    if unused:
        print(
            f"\n{len(unused)} unused function(s), method(s), class(es) or propert(ies). Delete them, or, if"
            f" something calls them by name, add them to {ALLOWLIST.relative_to(ROOT)} saying who calls them."
        )
        return 1
    print("No new unused functions, methods, classes or properties.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
