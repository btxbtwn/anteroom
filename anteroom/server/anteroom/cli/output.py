"""Stdout for people and for ``--json``."""

from __future__ import annotations

import json
import sys
from typing import Any


def emit(payload: dict[str, Any], *, as_json: bool, human: str | None = None) -> None:
    if as_json:
        sys.stdout.write(json.dumps(payload, indent=2, default=str) + "\n")
        return
    if human is not None:
        sys.stdout.write(human if human.endswith("\n") else human + "\n")
        return
    data = payload.get("data") if "data" in payload else payload
    sys.stdout.write(json.dumps(data, indent=2, default=str) + "\n")


def note(message: str) -> None:
    sys.stderr.write(message.rstrip() + "\n")


def envelope(*, ok: bool, status: int | None, data: Any = None, error: str | None = None, **extra: Any) -> dict[str, Any]:
    body: dict[str, Any] = {"ok": ok, "status": status}
    if error:
        body["error"] = error
    if data is not None or ok:
        body["data"] = data
    body.update(extra)
    return body
