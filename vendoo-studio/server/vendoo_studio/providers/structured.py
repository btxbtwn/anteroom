"""Small provider-native schemas for scoped listing repairs."""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict


class RepairOperation(BaseModel):
    model_config = ConfigDict(extra="forbid")
    op: Literal["add", "replace"]
    path: str
    value: str | float | bool | list[str | float] | None


class RepairReply(BaseModel):
    model_config = ConfigDict(extra="forbid")
    patches: list[RepairOperation]


def repair_schema(paths: list[str]) -> dict:
    schema = RepairReply.model_json_schema()
    schema["$defs"]["RepairOperation"]["properties"]["path"]["enum"] = paths
    return schema
