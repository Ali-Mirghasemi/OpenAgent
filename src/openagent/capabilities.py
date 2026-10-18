"""Adapter-reported capabilities: unknown is not the same as unsupported."""
from __future__ import annotations

from typing import Literal
from pydantic import BaseModel, Field

State = Literal["supported", "unsupported", "unknown"]
Evidence = Literal["declared", "observed", "tested"]


class Feature(BaseModel):
    state: State = "unknown"
    evidence: Evidence | None = None
    note: str | None = None


class Capabilities(BaseModel):
    provider: str
    model: str
    transport: Literal["mock", "browser", "oauth", "api"]
    authentication: str
    streaming: Feature = Field(default_factory=Feature)
    native_tools: Feature = Field(default_factory=Feature)
    prompt_tools: Feature = Field(default_factory=Feature)
    persistent_chat: Feature = Field(default_factory=Feature)
    projects: Feature = Field(default_factory=Feature)
    file_upload: Feature = Field(default_factory=Feature)
    file_download: Feature = Field(default_factory=Feature)
    interruption: Feature = Field(default_factory=Feature)
    reasoning_levels: list[str] = Field(default_factory=list)
