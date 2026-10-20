"""Experimental, opt-in ChatGPT *public UI* adapter.

No private endpoints, cookie extraction, login automation or model/plan guesses.
A model ID of ``chatgpt-web/default`` means the account's CURRENT UI selection;
it does not identify or select an underlying OpenAI model.

The implementation is intentionally fail-closed: if the current UI does not
expose reliable, turn-scoped completion evidence, we return an error rather than
a partial answer. This is NOT a verified production ChatGPT integration.
"""
from __future__ import annotations

import asyncio
import time
from typing import Any

from ..browser.diagnostics import UIObservation, UISelectors, inspect_ui
from ..browser.manager import BrowserManager
from ..capabilities import Capabilities, Feature
from ..models import Message, ProviderOutput
from .browser_ui import BrowserSiteProfile, BrowserUIError, BrowserUIProvider

CHATGPT_MODEL = "chatgpt-web/default"
CHATGPT_URL = "https://chatgpt.com/"

# Candidate selectors from *public UI*, deliberately not internal service APIs.
# UI updates/localization can invalidate them. Always verify on the actual
# user's account; no selector implies a particular plan or login succeeded.
CHATGPT_SELECTORS = UISelectors(
    composer="#prompt-textarea",
    send_button='button[data-testid="send-button"]',
    assistant_message='[data-message-author-role="assistant"]',
    login_required='a[href*="/auth/login"], button[data-testid="login-button"]',
    security_challenge='iframe[src*="challenges.cloudflare.com"], [id*="challenge-running"]',
    rate_limited='[data-testid="rate-limit-error"]',
    generation_in_progress='button[data-testid="stop-button"]',
    response_complete='button[data-testid="copy-turn-action-button"]',
)

# Only the *new* assistant turn's own enclosing article is considered complete.
# A copy button on an earlier turn cannot complete the current request.
TURN_COPY_BUTTON = (
    'button[data-testid="copy-turn-action-button"],'
    'button[aria-label*="Copy message"]'
)
TURN_PENDING = (
    '[aria-busy="true"], [class*="loading-shimmer"],'
    ' [class*="result-thinking"]'
)


class ChatGPTWebProvider(BrowserUIProvider):
    """One-shot selected-model chat, with one request at a time per account."""

    def __init__(self, browser: BrowserManager, *, timeout_s: float = 120.0,
                 poll_s: float = 0.25, stable_observations: int = 4,
                 url: str = CHATGPT_URL, selectors: UISelectors = CHATGPT_SELECTORS):
        # `url` / `selectors` injection is specifically for deterministic tests,
        # never a way to silently redirect the production adapter elsewhere.
        if url != CHATGPT_URL and not url.startswith("http://127.0.0.1:"):
            raise ValueError("ChatGPT adapter must use chatgpt.com or a local fixture")
        site = BrowserSiteProfile(
            provider_id="chatgpt-web", model_id=CHATGPT_MODEL, url=url,
            selectors=selectors, timeout_s=timeout_s, poll_s=poll_s,
            stable_observations=stable_observations,
        )
        super().__init__(site, browser)
        self._turn_lock = asyncio.Lock()

    async def inspect(self) -> UIObservation:
        # Readiness is VISUAL only; it is not proof of authentication.
        await self.browser.start()
        return await super().inspect()

    async def complete(self, model: str, messages: list[Message],
                       tools: list[dict] | None = None) -> ProviderOutput:
        # Serializing requests is safer than interleaving ChatGPT UI turns in
        # one profile. Cancellation still propagates into tab cleanup.
        if model != CHATGPT_MODEL:
            raise BrowserUIError("invalid_model", "unknown model")
        if tools:
            raise BrowserUIError("unsupported_tools", "native tools are not supported")
        async with self._turn_lock:
            await self.browser.start()
            return await super().complete(model, messages, tools=tools)

    async def capabilities(self, model: str) -> Capabilities:
        if model != CHATGPT_MODEL:
            raise ValueError("unknown model")
        unknown = Feature(state="unknown", note="requires live account and UI verification")
        unsupported = Feature(state="unsupported", evidence="declared")
        return Capabilities(
            provider=self.id, model=model, transport="browser",
            authentication="manual login in OpenAgent browser profile; UI-only check",
            streaming=unsupported, native_tools=unsupported,
            prompt_tools=unknown, persistent_chat=unsupported,
            projects=unknown, file_upload=unknown, file_download=unknown,
            interruption=unknown, reasoning_levels=[],
        )

    async def _wait_for_answer(self, page: Any, baseline: int,
                               done_was_absent: bool) -> str:
        """Require new assistant text AND per-turn completion, not idle time."""
        del done_was_absent  # global copy buttons can belong to *old* turns
        deadline = time.monotonic() + self.site.timeout_s
        previous = None
        stable = 0
        while time.monotonic() < deadline:
            state = await inspect_ui(page, self.site.selectors)
            if state.state in (
                "login_required", "security_challenge", "rate_limited",
                "browser_disconnected",
            ):
                raise BrowserUIError(state.state, "ChatGPT UI is not available: " + state.state)

            turns = page.locator(self.site.selectors.assistant_message)
            count = await turns.count()
            if count > baseline:
                new_turn = turns.nth(count - 1)
                # Check only the newly anchored turn. The article may own the
                # copy control as a sibling of the markdown body.
                article = new_turn.locator("xpath=ancestor::article[1]")
                scope = article if await article.count() else new_turn
                copy_ready = await scope.locator(TURN_COPY_BUTTON).first.is_visible()
                pending = await new_turn.locator(TURN_PENDING).count() > 0
                rendering = state.state == "generation_in_progress"
                markdown = new_turn.locator(".markdown")
                text = ((await markdown.first.inner_text()) if await markdown.count()
                        else (await new_turn.inner_text())).strip()
                if text and text == previous:
                    stable += 1
                else:
                    previous, stable = text, 1 if text else 0
                if stable >= self.site.stable_observations and copy_ready and not (pending or rendering):
                    return text
            await asyncio.sleep(self.site.poll_s)
        raise BrowserUIError(
            "generation_timeout",
            "ChatGPT did not provide verifiable completed-turn evidence; remote outcome unknown",
        )
