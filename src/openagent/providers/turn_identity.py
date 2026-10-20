"""Provider-neutral DOM turn identity tracking for virtualized conversations.

This module observes only DOM attributes supplied by the UI. It never reads
cookies, hidden tokens, or private provider endpoints. Missing/unstable IDs
fall back to a conservative increase-in-message-count check.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any


MAX_SCAN = 256


@dataclass(frozen=True)
class TurnBaseline:
    count: int
    ids: frozenset[str]


async def turn_id(node: Any) -> str | None:
    """Prefer a stable logical turn ID over shifting DOM positions.

    Chat UIs differ about whether the ID is on the answer or an ancestor.
    Fail closed when the page does not expose a usable ID.
    """
    for candidate in (node, node.locator("xpath=ancestor::article[1]")):
        getter = getattr(candidate, "get_attribute", None)
        if not callable(getter):
            continue
        for name in ("data-turn-id", "data-message-id"):
            try:
                identifier = await getter(name)
            except Exception:
                identifier = None
            if isinstance(identifier, str) and 0 < len(identifier.strip()) <= 128:
                return identifier.strip()
    return None


async def snapshot_turns(page: Any, selector: str) -> TurnBaseline:
    turns = page.locator(selector)
    count = await turns.count()
    start = max(0, count - MAX_SCAN)
    identities = set()
    for index in range(start, count):
        ident = await turn_id(turns.nth(index))
        if ident:
            identities.add(ident)
    return TurnBaseline(count=count, ids=frozenset(identities))


async def find_new_turn(page: Any, selector: str, baseline: TurnBaseline) -> tuple[Any, str | None] | None:
    """Select a new answer by stable ID, otherwise only by a count increase.

    If history virtualizes and replaces an old node without exposing an ID,
    we cannot prove it is a new answer and deliberately keep waiting.
    """
    turns = page.locator(selector)
    count = await turns.count()
    for index in range(count - 1, max(-1, count - MAX_SCAN - 1), -1):
        node = turns.nth(index)
        ident = await turn_id(node)
        if ident and ident not in baseline.ids:
            # A baseline with zero IDs cannot establish that the "new" ID is
            # new; it may just be an old node gaining an attribute during rerender.
            if baseline.ids or count > baseline.count:
                return node, ident
    if count > baseline.count:
        node = turns.nth(count - 1)
        return node, await turn_id(node)
    return None
