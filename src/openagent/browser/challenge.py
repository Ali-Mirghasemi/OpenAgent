"""Passive, narrowly scoped detection of hosted human-verification pages.

Never clicks or answers a challenge, reads cookies, or exposes page content.
The probe is opt-in by the site adapter, because ordinary conversations can
contain the same phrases without being verification pages.
"""
from __future__ import annotations

from typing import Any


_CHALLENGE_DOM_PROBE = r"""() => {
  // Normal assistant/chat UI wins over text-based challenge heuristics. A
  // user can legitimately discuss Cloudflare in a conversation.
  if (document.querySelector(`
    #prompt-textarea, [data-testid="prompt-textarea"],
    [data-user-message-bubble], [data-message-author-role],
    [data-conversation-role="assistant"], [data-conversation-role="user"]
  `)) return null;
  const title = (document.title || '').trim().toLowerCase();
  if (/^just a moment(?:[.\u2026]*)?$/.test(title)) return 'challenge_title';
  const marker = document.querySelector(`
    #challenge-form, #challenge-stage, #challenge-running,
    #cf-challenge-running, #cf-wrapper, #challenge-widget-container,
    #challenge-body-text, #turnstile-wrapper,
    input[name="cf-turnstile-response"],
    iframe[src*="challenges.cloudflare.com"],
    script[src*="/cdn-cgi/challenge-platform/"]
  `);
  // Script alone is not conclusive; scripts may persist across app loads.
  if (marker && marker.tagName !== 'SCRIPT') return 'challenge_markup';
  const text = (document.body?.innerText || '').trim();
  if (text.length <= 1500 && /(?:^|\n)\s*(?:verify you are human|verifying you are human|checking your browser before accessing|please verify you are human)\s*(?:\n|$)/i.test(text))
    return 'human_verification_prompt';
  return null;
}"""


async def inspect_human_verification(page: Any) -> str | None:
    """Return a fixed evidence code for a probable challenge, or None.

    This is not an anti-bot workaround and cannot prove why a challenge arose.
    Browsers/test doubles without evaluate() remain supported.
    """
    if page.is_closed():
        return None
    evaluate = getattr(page, 'evaluate', None)
    if not callable(evaluate):
        return None
    try:
        result = await evaluate(_CHALLENGE_DOM_PROBE)
        return result if result in (
            'challenge_title', 'challenge_markup', 'human_verification_prompt'
        ) else None
    except Exception:
        return None
