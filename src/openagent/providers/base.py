"""Provider adapter interface: no execution, filesystem access, or shell tools here."""
from __future__ import annotations

from abc import ABC, abstractmethod
from ..capabilities import Capabilities
from ..models import Message, ProviderOutput


class Provider(ABC):
    id: str
    # An adapter must explicitly opt in only after its upstream continuation
    # logic has been implemented and verified. Default is local history only.
    supports_upstream_conversations: bool = False
    supports_project_messages: bool = False

    @abstractmethod
    async def models(self) -> list[str]:
        """Return fully-qualified model IDs, e.g. mock/echo."""

    @abstractmethod
    async def capabilities(self, model: str) -> Capabilities:
        """Return effective capability information. No inferred booleans."""

    @abstractmethod
    async def complete(
        self,
        model: str,
        messages: list[Message],
        tools: list[dict] | None = None,
    ) -> ProviderOutput:
        """Produce one reply; preserve the caller's ordered message history."""

    async def complete_in_session(
        self,
        model: str,
        messages: list[Message],
        *,
        upstream_conversation_id: str | None,
    ) -> ProviderOutput:
        """Complete a locally tracked turn, or explicitly resume an upstream chat.

        One-shot providers receive the full local transcript when unbound.
        Bound sessions MUST NOT silently start a fresh upstream chat.
        """
        if upstream_conversation_id is not None:
            raise RuntimeError("upstream continuation is not implemented for this provider")
        return await self.complete(model, messages)

    async def complete_in_project_session(
        self, model: str, messages: list[Message], *, project_id: str,
        project_name: str, upstream_conversation_id: str | None,
    ) -> ProviderOutput:
        """No implicit fallback to non-project conversation transport."""
        raise RuntimeError("Project-scoped messages are not implemented for this provider")
