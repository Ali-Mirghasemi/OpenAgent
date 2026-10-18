"""Deterministic transport for contract testing, not a real AI model."""
from __future__ import annotations

from ..capabilities import Capabilities, Feature
from ..models import Message, ProviderOutput
from .base import Provider


class MockProvider(Provider):
    id = "mock"
    MODEL = "mock/echo"

    async def models(self) -> list[str]:
        return [self.MODEL]

    async def capabilities(self, model: str) -> Capabilities:
        if model != self.MODEL:
            raise ValueError("unknown model")
        yes = Feature(state="supported", evidence="tested")
        no = Feature(state="unsupported", evidence="tested")
        return Capabilities(
            provider=self.id,
            model=model,
            transport="mock",
            authentication="none",
            streaming=yes,  # HTTP SSE response, delivered as one synthetic chunk.
            native_tools=no,
            prompt_tools=no,
            persistent_chat=yes,  # OpenAgent local session storage, not provider-side chat.
            projects=no,
            file_upload=no,
            file_download=no,
            interruption=no,
        )

    async def complete(
        self,
        model: str,
        messages: list[Message],
        tools: list[dict] | None = None,
    ) -> ProviderOutput:
        if model != self.MODEL:
            raise ValueError("unknown model")
        if tools:
            raise ValueError("mock provider does not support native tools")
        last = next((m for m in reversed(messages) if m.role == "user"), None)
        text = last.content if last is not None else "(no user message)"
        if not isinstance(text, str):
            text = "(non-text input received)"
        return ProviderOutput(content=f"Echo: {text}")
