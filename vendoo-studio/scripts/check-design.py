#!/usr/bin/env python3
"""Fail when frontend code adds a pattern DESIGN.md rules out.

    python scripts/check-design.py                  # check
    python scripts/check-design.py --write-baseline # rewrite the baseline

Counts each rule's matches per file under src/ (tests excluded). design_baseline.json
records the counts that were already there when the check was added. A file may not
go above its recorded count, and a new file starts at zero. The record only shrinks:
when you fix a recorded case, run --write-baseline and commit the smaller record.
Never raise a count to make the check pass; use the shared component or theme
token DESIGN.md names instead.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
BASELINE = ROOT / "design_baseline.json"
# Where the theme defines its tokens. Colors and weights belong here.
TOKEN_FILES = {"src/styles/t3-theme.css"}
# Logos draw a brand's own colors, which DESIGN.md allows in logos only.
LOGO_FILES = {
    "src/components/marketplaceLogos.ts",
    "src/components/MarketplaceLogo.tsx",
    "src/components/providerIcons.tsx",
}
# NativeSelect: the one native <select>, kept on purpose for long marketplace option
# lists so phones show their own picker. Other code uses it instead of a raw <select>.
NATIVE_SELECT_FILES = {"src/components/ListingFormControls.tsx"}

RULES: dict[str, tuple[str, re.Pattern[str], tuple[str, ...]]] = {
    "hardcoded-color": (
        "a hex or rgb() color; use a theme token (text-muted-foreground, var(--border), …)",
        re.compile(r"#(?:[0-9a-fA-F]{3}|[0-9a-fA-F]{6}|[0-9a-fA-F]{8})\b(?![\w-])|\brgba?\(\s*\d"),
        (".css", ".tsx", ".ts"),
    ),
    "uppercase-label": (
        "an uppercase label; labels are sentence case",
        re.compile(r"text-transform:\s*uppercase|(?<![\w-])uppercase(?![\w-])"),
        (".css", ".tsx"),
    ),
    "heavy-weight": (
        "a weight above 600; use 400, 500 or 600",
        re.compile(r"font-weight:\s*(?:[7-9]00|bold|bolder)\b|(?<![\w-])font-(?:bold|extrabold|black)(?![\w-])"),
        (".css", ".tsx"),
    ),
    "gradient": (
        "a gradient; surfaces are flat",
        re.compile(r"(?:linear|radial|conic)-gradient\(|(?<![\w-])bg-(?:linear|radial|conic|gradient)-"),
        (".css", ".tsx"),
    ),
    "native-control": (
        "a native <select>, checkbox or radio; use Select, Checkbox or RadioGroup from ui/ (or NativeSelect for a long marketplace option list)",
        re.compile(r"<select\b|type=\"(?:checkbox|radio)\""),
        (".tsx",),
    ),
    "arbitrary-text-size": (
        "an arbitrary text size; use the type scale (text-2xs, text-xs, text-sm, …)",
        re.compile(r"(?<![\w-])text-\[\d+(?:\.\d+)?px\]"),
        (".tsx",),
    ),
}


def source_files() -> list[Path]:
    files = []
    for path in sorted(SRC.rglob("*")):
        if not path.is_file() or ".test." in path.name:
            continue
        if path.suffix in {".css", ".tsx", ".ts"}:
            files.append(path)
    return files


def measure() -> dict[str, dict[str, int]]:
    counts: dict[str, dict[str, int]] = {}
    for path in source_files():
        rel = path.relative_to(ROOT).as_posix()
        text = path.read_text(encoding="utf-8")
        for rule, (_, pattern, suffixes) in RULES.items():
            if path.suffix not in suffixes:
                continue
            if rel in TOKEN_FILES and rule in {"hardcoded-color", "heavy-weight"}:
                continue
            if rel in LOGO_FILES and rule == "hardcoded-color":
                continue
            if rel in NATIVE_SELECT_FILES and rule == "native-control":
                continue
            found = len(pattern.findall(text))
            if found:
                counts.setdefault(rel, {})[rule] = found
    return counts


def first_lines(rel: str, rule: str, limit: int = 3) -> list[str]:
    pattern = RULES[rule][1]
    lines = (ROOT / rel).read_text(encoding="utf-8").splitlines()
    hits = [f"    {rel}:{number}: {line.strip()[:110]}" for number, line in enumerate(lines, 1) if pattern.search(line)]
    return hits[:limit]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--write-baseline", action="store_true", help="record the current counts")
    args = parser.parse_args()

    current = measure()
    if args.write_baseline:
        BASELINE.write_text(json.dumps(current, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        total = sum(sum(rules.values()) for rules in current.values())
        print(f"Wrote {BASELINE.name}: {total} recorded cases in {len(current)} files.")
        return 0

    baseline: dict[str, dict[str, int]] = json.loads(BASELINE.read_text(encoding="utf-8"))
    added: list[str] = []
    fixed: list[str] = []
    for rel in sorted(set(current) | set(baseline)):
        for rule in RULES:
            now = current.get(rel, {}).get(rule, 0)
            before = baseline.get(rel, {}).get(rule, 0)
            if now > before:
                added.append(f"  {rel}: {now - before} new {RULES[rule][0]}")
                added.extend(first_lines(rel, rule))
            elif now < before:
                fixed.append(f"  {rel}: {rule} {before} → {now}")

    if added:
        print("New design-rule violations (see DESIGN.md):")
        print("\n".join(added))
    if fixed:
        print("Recorded cases were fixed. Shrink the record with `python scripts/check-design.py --write-baseline`:")
        print("\n".join(fixed))
    if added or fixed:
        return 1
    print("No new design-rule violations.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
