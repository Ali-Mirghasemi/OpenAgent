"""Wire models shared by the compatible API and provider interface."""
from __future__ import annotations

from typing import Any, Literal
from pydantic import BaseModel, ConfigDict, Field, model_validator


class Message(BaseModel):
    """Preserves system/user/assistant/tool roles and native tool-call fields."""

    model_config = ConfigDict(extra="allow")
    role: Literal["system", "developer", "user", "assistant", "tool"]
    content: str | list[dict[str, Any]] | None = None
    name: str | None = None
    tool_call_id: str | None = None
    tool_calls: list[dict[str, Any]] | None = None

    @model_validator(mode="after")
    def check_payload(self) -> "Message":
        if self.content is None and not self.tool_calls:
            raise ValueError("message must include content or tool_calls")
        return self


class CompletionRequest(BaseModel):
    model: str
    messages: list[Message] = Field(min_length=1, max_length=256)
    stream: bool = False
    tools: list[dict[str, Any]] | None = None
    tool_choice: Any | None = None
    temperature: float | None = None


class ProviderOutput(BaseModel):
    content: str | None = None
    tool_calls: list[dict[str, Any]] | None = None
    finish_reason: Literal["stop", "tool_calls", "length"] = "stop"


class SessionCreate(BaseModel):
    model: str


class SessionTurn(BaseModel):
    request_id: str = Field(min_length=1, max_length=128)
    message: Message
