"""Provider adapter interface: no execution, filesystem access, or shell tools here."""
from __future__ import annotations

from abc import ABC, abstractmethod
from ..capabilities import Capabilities
from ..models import Message, ProviderOutput


class Provider(ABC):
    id: str

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
