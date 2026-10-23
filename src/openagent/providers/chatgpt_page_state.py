"""Read-only, fail-closed classification of ChatGPT *page-level* failures.

Only trusted top-level error surfaces are considered. Chat text is never returned,
logged, or searched for as an error when any conversation turn is rendered.
No click, retry, navigation, credential, or browser-storage operations occur.
"""
from __future__ import annotations

import re
from typing import Any

# Keep text recognition narrow; product copy is not a stable API.
_ACCESS = re.compile(
    r"^(?:you (?:(?:cannot|can not|can't)|do not|don't) (?:access|have access to) (?:this |the )?chat"
    r"|you (?:do not|don't) have (?:access|permission) to (?:this |the )?(?:chat|conversation)"
    r"|access to (?:this |the )?(?:chat|conversation) (?:is )?denied)\b",
    re.IGNORECASE,
)
_UNAVAILABLE = re.compile(
    r"^(?:(?:this |the )?(?:chat|conversation) (?:is )?(?:not available|isn't available|unavailable|not found)"
    r"|(?:this |the )?(?:chat|conversation) (?:could not|couldn't|cannot|can't) be (?:found|loaded)"
    r"|(?:unable|failed) to load (?:this |the )?(?:chat|conversation))\b",
    re.IGNORECASE,
)
# Narrowly-scoped, no page-wide text when an actual chat is rendered.
_CHAT_MARKERS = (
    'main [data-user-message-bubble], main [data-message-author-role],'
    'main [data-conversation-role="user"], main [data-conversation-role="assistant"],'
    'main [data-turn-key], main article[data-testid^="conversation-turn-"]'
)
_ERROR_SURFACES = (
    'main [role="alert"], main h1, main h2, main [data-testid="conversation-error"],'
    'main [data-testid="chat-error"], main [data-testid="error-page"]'
)


def _classify(text: str) -> str | None:
    # Strip repeated whitespace, not punctuation or adjacent text.
    value = ' '.join(text.split()).strip()
    if len(value) > 180:
        return None
    if _ACCESS.search(value):
        return 'conversation_access_denied'
    if _UNAVAILABLE.search(value):
        return 'conversation_unavailable'
    return None


async def chatgpt_page_error(page: Any) -> str | None:
    """Return a finite error code if an authenticated chat cannot be loaded.

    Returns None for normal page/unknown state; callers must still independently
    verify the expected conversation URL, role-marked turns, and completion.
    DOM parsing errors must not accidentally indicate an accessible chat.
    """
    if page.is_closed():
        return 'browser_disconnected'
    # For unit-test doubles that lack real text APIs, preserve current behavior.
    try:
        if await page.locator(_CHAT_MARKERS).count():
            return None
        markers = page.locator(_ERROR_SURFACES)
        for i in range(min(12, await markers.count())):
            item = markers.nth(i)
            if await item.is_visible():
                code = _classify(await item.inner_text())
                if code:
                    return code
        # A small error page may have plain text with no heading/alert markup.
        main = page.locator('main')
        if await main.count():
            content = await main.first.inner_text()
            if len(content) <= 180:
                return _classify(content)
    except (AttributeError, TypeError, NotImplementedError):
        return None
    return None


# Read-only control inventory: *presence* does not imply feature support.
_FEATURE_SELECTORS = {
    'model_picker': 'button[data-testid="model-switcher-dropdown-button"], button[aria-label*="model" i][aria-haspopup], [data-testid="model-switcher"]',
    'effort_picker': 'button[data-testid*="effort" i], button[aria-label*="reasoning" i], button[aria-label*="thinking" i]',
    'attachment_button': 'button[aria-label*="attach" i], button[aria-label*="upload" i], button[data-testid*="attachment" i]',
    'file_input': 'input[type="file"]',
    'voice_button': 'button[aria-label*="voice" i], button[aria-label*="dictat" i]',
    'tools_menu': 'button[data-testid*="tools" i], button[aria-label*="tools" i]',
}


async def chatgpt_feature_inventory(page: Any) -> dict[str, str]:
    """Expose presence only; no picker opening or file access.

    Deliberately reports 'not_detected' instead of 'unsupported' because
    controls can be hidden behind menus, plans and localized labels.
    """
    from ..browser.diagnostics import _any_visible

    result: dict[str, str] = {}
    for feature, selector in _FEATURE_SELECTORS.items():
        try:
            # File inputs are intentionally hidden in many UIs. Their presence
            # is not evidence that uploading is permitted or functional.
            if feature == 'file_input':
                detected = bool(await page.locator(selector).count())
            else:
                detected = await _any_visible(page, selector)
            result[feature] = 'detected' if detected else 'not_detected'
        except Exception:
            result[feature] = 'unavailable'
    return result
