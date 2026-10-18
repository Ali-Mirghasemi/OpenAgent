"""Single source of truth for model resolution and provider registration."""
from __future__ import annotations

from .providers.base import Provider


class Registry:
    def __init__(self) -> None:
        self._providers: dict[str, Provider] = {}

    def register(self, provider: Provider) -> None:
        if provider.id in self._providers:
            raise ValueError(f"duplicate provider {provider.id}")
        self._providers[provider.id] = provider

    async def resolve(self, model: str) -> Provider:
        provider_id, sep, _ = model.partition("/")
        if not sep or provider_id not in self._providers:
            raise KeyError(model)
        provider = self._providers[provider_id]
        if model not in await provider.models():
            raise KeyError(model)
        return provider

    async def all_models(self) -> list[str]:
        result: list[str] = []
        for provider in self._providers.values():
            result.extend(await provider.models())
        return result
