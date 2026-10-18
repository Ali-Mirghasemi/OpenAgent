"""Parse OAP/1 compact tool envelopes; this module NEVER runs tools."""
from __future__ import annotations

import json
from typing import Any
from pydantic import BaseModel, ConfigDict, Field, model_validator


class ToolCall(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: str = Field(min_length=1, max_length=128)
    tool: str = Field(min_length=1, max_length=96)
    args: dict[str, Any] = Field(default_factory=dict)


class ToolEnvelope(BaseModel):
    model_config = ConfigDict(extra="forbid")
    v: int = Field(ge=1, le=1)
    calls: list[ToolCall] = Field(min_length=1, max_length=32)
    mode: str = "sequential"
    stop_on_error: bool = True

    @model_validator(mode="after")
    def validate_batch(self) -> "ToolEnvelope":
        if self.mode not in ("sequential", "parallel"):
            raise ValueError("unknown batch mode")
        ids = [call.id for call in self.calls]
        if len(ids) != len(set(ids)):
            raise ValueError("duplicate tool-call IDs")
        return self


def parse_tool_envelope(raw: str, *, allowed_tools: set[str]) -> ToolEnvelope:
    """Fail closed on undeclared tools; caller remains responsible for authorization."""
    if len(raw.encode("utf-8")) > 1_048_576:
        raise ValueError("envelope exceeds 1 MiB")
    data = json.loads(raw)
    if not isinstance(data, dict):
        raise ValueError("envelope must be a JSON object")
    if "tool" in data and "calls" not in data:
        data = {"v": data.get("v"), "calls": [
            {key: data[key] for key in ("id", "tool", "args") if key in data}
        ]}
    envelope = ToolEnvelope.model_validate(data)
    unknown = sorted({c.tool for c in envelope.calls} - allowed_tools)
    if unknown:
        raise ValueError(f"undeclared tool(s): {', '.join(unknown)}")
    return envelope
