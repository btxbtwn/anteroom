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
from collections.abc import Iterable
from pathlib import Path


def image_tag(machine: dict) -> str:
    tag = (machine.get("image_ref") or {}).get("tag")
    if tag:
        return tag
    image = (machine.get("config") or {}).get("image", "")
    name = image.rsplit("/", 1)[-1].split("@", 1)[0]
    return name.split(":", 1)[1] if ":" in name else ""


def image_digest(machine: dict) -> str:
    digest = (machine.get("image_ref") or {}).get("digest")
    if isinstance(digest, str) and digest:
        return digest
    image = (machine.get("config") or {}).get("image", "")
    if "@" in image:
        return image.rsplit("@", 1)[1]
    return ""


def problems(
    machines: list[dict],
    label: str,
    *,
    required_groups: Iterable[str] = (),
    expected_digest: str | None = None,
) -> tuple[dict[str, int], list[str]]:
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
        if expected_digest is not None:
            digest = image_digest(machine)
            if digest != expected_digest:
                wrong.append(f"{name} has digest '{digest}', expected '{expected_digest}'")
        if machine.get("state") != "started":
            wrong.append(f"{name} is {machine.get('state')}")
    if not groups:
        wrong.append("no Machines found")
    for required in required_groups:
        if groups.get(required, 0) < 1:
            wrong.append(f"required process group '{required}' has no Machines")
    return dict(groups), wrong


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--label", required=True)
    parser.add_argument(
        "--require-group",
        action="append",
        default=[],
        help="Process group that must have at least one started Machine (repeatable).",
    )
    parser.add_argument("--digest", default=None, help="Optional exact OCI image digest every Machine must run.")
    parser.add_argument("machines", type=Path, help="Output of flyctl machines list --json")
    args = parser.parse_args()
    groups, wrong = problems(
        json.loads(args.machines.read_text() or "[]"),
        args.label,
        required_groups=args.require_group,
        expected_digest=args.digest,
    )
    for line in wrong:
        print(line)
    if wrong:
        return 1
    print(", ".join(f"{group}: {count} started" for group, count in sorted(groups.items())) + f" on {args.label}.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
