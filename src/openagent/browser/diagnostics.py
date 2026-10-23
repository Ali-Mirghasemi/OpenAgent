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
    # Some UIs hide/disable Send until the composer has input. Explicit opt-in
    # avoids relaxing readiness rules for ordinary browser providers.
    send_button_may_appear_after_input: bool = False
    # Explicit provider opt-in: inspect challenge-page DOM without reading chat.
    human_verification_probe: bool = False

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


async def _any_visible(page: PageLike, selector: str | None) -> bool:
    """Check ALL candidate matches, not just the first (possibly hidden) one.

    Live apps often render hidden mobile and desktop copies of a control.
    Keeping the match search bounded prevents pathological DOM scans. Simple
    test adapters without count()/nth() retain their existing .first contract.
    """
    if not selector:
        return False
    locator = page.locator(selector)
    if callable(getattr(locator, "count", None)) and callable(getattr(locator, "nth", None)):
        for index in range(min(await locator.count(), 32)):
            if await locator.nth(index).is_visible():
                return True
        return False
    return await locator.first.is_visible()


async def inspect_ui(page: PageLike, selectors: UISelectors) -> UIObservation:
    """Check high-priority status markers before claiming UI readiness.

    The provider must supply selectors and verify that they are specific to
    that site's current UI. Hidden or stale elements aren't treated as proof.
    """
    if page.is_closed():
        return UIObservation("browser_disconnected", "page_closed")

    async def visible(selector: str | None) -> bool:
        return await _any_visible(page, selector)

    try:
        if selectors.human_verification_probe:
            from .challenge import inspect_human_verification
            evidence = await inspect_human_verification(page)
            if evidence:
                return UIObservation("security_challenge", evidence)
        for field, state in (
            ("security_challenge", "security_challenge"),
            ("login_required", "login_required"),
            ("rate_limited", "rate_limited"),
            ("generation_in_progress", "generation_in_progress"),
        ):
            if await visible(getattr(selectors, field)):
                return UIObservation(state, field)
        if await visible(selectors.composer):
            if await visible(selectors.send_button):
                return UIObservation("ready", "composer_and_send_button")
            if selectors.send_button_may_appear_after_input:
                # UI readiness only, not authentication proof. Login markers
                # were checked first, but may be localized or unavailable.
                return UIObservation("ready", "composer_visible_send_inactive")
    except Exception:
        # A detached/changed DOM is not an excuse to return 'ready'.
        return UIObservation("ui_changed", "selector_unavailable")
    return UIObservation("ui_changed", "expected_controls_missing")


async def inspect_ui_controls(page: PageLike, selectors: UISelectors) -> dict[str, str]:
    """Read-only selector visibility for troubleshooting a changed site UI.

    Returns fixed field names and states only: no DOM text, cookies, browser
    storage, URL query strings, or raw selector errors are exposed.
    An invisible control may be absent, hidden, or in a different layout.
    """
    result: dict[str, str] = {}
    for field in (
        "composer", "send_button", "assistant_message", "login_required",
        "security_challenge", "rate_limited", "generation_in_progress",
        "response_complete",
    ):
        selector = getattr(selectors, field)
        if not selector:
            result[field] = "not_configured"
            continue
        if page.is_closed():
            result[field] = "unavailable"
            continue
        try:
            result[field] = (
                "visible" if await _any_visible(page, selector)
                else "not_visible"
            )
        except Exception:
            result[field] = "unavailable"
    return result
