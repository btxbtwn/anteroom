#!/usr/bin/env python3
"""Exit 0 only when every Machine of the app runs the given image label and is started.

Reads `flyctl machines list --json`. Checks each process group (the web `app`
group and any group without a public service, such as a worker), because the
public health URL only reaches the web Machines. One-off Machines such as
`fly console` sessions (auto-destroyed, or in Fly's own `fly_` groups) are
left out.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import defaultdict
from pathlib import Path


def image_tag(machine: dict) -> str:
    tag = (machine.get("image_ref") or {}).get("tag")
    if tag:
        return tag
    image = (machine.get("config") or {}).get("image", "")
    name = image.rsplit("/", 1)[-1].split("@", 1)[0]
    return name.split(":", 1)[1] if ":" in name else ""


def problems(machines: list[dict], label: str) -> tuple[dict[str, int], list[str]]:
    """(Machines per process group, what is wrong)."""
    groups: dict[str, int] = defaultdict(int)
    wrong: list[str] = []
    for machine in machines:
        config = machine.get("config") or {}
        group = (config.get("metadata") or {}).get("fly_process_group") or "app"
        if config.get("auto_destroy") or group.startswith("fly_"):
            continue
        groups[group] += 1
        name = f"{group} Machine {machine.get('id', '?')}"
        tag = image_tag(machine)
        if tag != label:
            wrong.append(f"{name} runs '{tag}', expected '{label}'")
        if machine.get("state") != "started":
            wrong.append(f"{name} is {machine.get('state')}")
    if not groups:
        wrong.append("no Machines found")
    return dict(groups), wrong


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--label", required=True)
    parser.add_argument("machines", type=Path, help="Output of flyctl machines list --json")
    args = parser.parse_args()
    groups, wrong = problems(json.loads(args.machines.read_text() or "[]"), args.label)
    for line in wrong:
        print(line)
    if wrong:
        return 1
    print(", ".join(f"{group}: {count} started" for group, count in sorted(groups.items())) + f" on {args.label}.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
