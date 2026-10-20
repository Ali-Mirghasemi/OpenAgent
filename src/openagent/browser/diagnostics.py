"""Read-only, provider-neutral health classification for a browser page.

Selectors are supplied by an adapter, never guessed to imply a login succeeded.
Nothing reads cookies, local storage, page text or credentials. A matching
marker provides *UI evidence* only, not a guarantee of account authentication.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, Protocol


UIState = Literal[
    "ready", "login_required", "security_challenge", "rate_limited",
    "generation_in_progress", "ui_changed", "browser_disconnected",
]


@dataclass(frozen=True)
class UISelectors:
    composer: str
    send_button: str
    assistant_message: str
    login_required: str | None = None
    security_challenge: str | None = None
    rate_limited: str | None = None
    generation_in_progress: str | None = None
    response_complete: str | None = None

    def __post_init__(self) -> None:
        for field in ("composer", "send_button", "assistant_message"):
            if not getattr(self, field).strip():
                raise ValueError(f"{field} selector is required")


@dataclass(frozen=True)
class UIObservation:
    state: UIState
    evidence: str  # selector category only; never include page content

    def as_dict(self) -> dict[str, str]:
        return {"state": self.state, "evidence": self.evidence}


class LocatorLike(Protocol):
    async def is_visible(self) -> bool: ...


class PageLike(Protocol):
    def is_closed(self) -> bool: ...
    def locator(self, selector: str) -> LocatorLike: ...


async def inspect_ui(page: PageLike, selectors: UISelectors) -> UIObservation:
    """Check high-priority status markers before claiming UI readiness.

    The provider must supply selectors and verify that they are specific to
    that site's current UI. Hidden or stale elements aren't treated as proof.
    """
    if page.is_closed():
        return UIObservation("browser_disconnected", "page_closed")

    async def visible(selector: str | None) -> bool:
        return bool(selector and await page.locator(selector).first.is_visible())

    try:
        for field, state in (
            ("security_challenge", "security_challenge"),
            ("login_required", "login_required"),
            ("rate_limited", "rate_limited"),
            ("generation_in_progress", "generation_in_progress"),
        ):
            if await visible(getattr(selectors, field)):
                return UIObservation(state, field)
        if await visible(selectors.composer) and await visible(selectors.send_button):
            return UIObservation("ready", "composer_and_send_button")
    except Exception:
        # A detached/changed DOM is not an excuse to return 'ready'.
        return UIObservation("ui_changed", "selector_unavailable")
    return UIObservation("ui_changed", "expected_controls_missing")
