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
import uuid
from urllib.parse import urlsplit
from typing import Any

from ..browser.diagnostics import UIObservation, UISelectors, inspect_ui
from ..browser.manager import BrowserManager, BrowserNavigationError, _origin
from ..capabilities import Capabilities, Feature
from ..models import Message, ProviderOutput
from .browser_ui import BrowserSiteProfile, BrowserUIError, BrowserUIProvider, serialize_messages
from .turn_identity import TurnBaseline, find_new_turn, snapshot_turns

CHATGPT_MODEL = "chatgpt-web/default"
CHATGPT_URL = "https://chatgpt.com/"

# Candidate selectors from *public UI*, deliberately not internal service APIs.
# UI updates/localization can invalidate them. Always verify on the actual
# user's account; no selector implies a particular plan or login succeeded.
CHATGPT_SELECTORS = UISelectors(
    # Candidate selectors for the current public UI. Some logged-out pages
    # display an editor without the old #prompt-textarea or send test ID.
    composer=(
        '#prompt-textarea, [data-testid="prompt-textarea"], '
        '[contenteditable="true"][role="textbox"], '
        'textarea[placeholder*="Ask ChatGPT" i], '
        'textarea[placeholder*="Message" i]'
    ),
    send_button=(
        'button[data-testid="send-button"], '
        'button[aria-label="Send message"], '
        'button[aria-label="Send prompt"]'
    ),
    assistant_message='[data-message-author-role="assistant"]',
    # Screenshot-verified logged-out layout (2026-10-20): plain "Log in"
    # buttons/links appear at the top right and in the sidebar. These public
    # controls are more useful than assuming a private data-testid exists.
    # Keep exact text matching to avoid matching chat content or other actions.
    login_required=(
        'a[href*="/auth/login"], button[data-testid="login-button"], '
        'button:text-is("Log in"), a:text-is("Log in"), '
        '[role="button"]:text-is("Log in"), '
        'button:text-is("Sign in"), a:text-is("Sign in")'
    ),
    security_challenge='iframe[src*="challenges.cloudflare.com"], [id*="challenge-running"]',
    rate_limited='[data-testid="rate-limit-error"]',
    generation_in_progress='button[data-testid="stop-button"]',
    response_complete='button[data-testid="copy-turn-action-button"]',
    send_button_may_appear_after_input=True,
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


def parse_chatgpt_conversation_id(url: str, *, expected_origin: str = "https://chatgpt.com") -> str | None:
    """Recognize only canonical /c/<uuid> navigation on the trusted origin.

    No query strings, shared chats, project routes, redirects to a different
    origin, or model-generated IDs are accepted as a session binding.
    """
    try:
        parsed = urlsplit(url)
        if _origin(url) != expected_origin or parsed.query or parsed.fragment:
            return None
        parts = parsed.path.split("/")
        if len(parts) != 3 or parts[1] != "c":
            return None
        identifier = parts[2]
        if str(uuid.UUID(identifier)) != identifier:
            return None
        return identifier
    except (ValueError, AttributeError, TypeError, BrowserNavigationError):
        return None


def chatgpt_conversation_url(origin: str, conversation_id: str) -> str:
    """Build a continuation URL only from a validated opaque identifier."""
    if origin != "https://chatgpt.com" and not origin.startswith(("http://127.0.0.1:", "http://localhost:")):
        raise BrowserUIError("invalid_conversation_id", "invalid conversation origin")
    if parse_chatgpt_conversation_id(f"{origin}/c/{conversation_id}", expected_origin=origin) != conversation_id:
        raise BrowserUIError("invalid_conversation_id", "invalid upstream conversation identifier")
    return f"{origin}/c/{conversation_id}"


class ChatGPTWebProvider(BrowserUIProvider):
    """One-shot selected-model chat, with one request at a time per account."""

    def __init__(self, browser: BrowserManager, *, timeout_s: float = 120.0,
                 poll_s: float = 0.25, stable_observations: int = 4,
                 url: str = CHATGPT_URL, selectors: UISelectors = CHATGPT_SELECTORS,
                 enable_conversation_continuation: bool = False):
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
        # An operator must separately enable this *fixture-verified* protocol.
        # It is not production-proven and is disabled by default.
        self.supports_upstream_conversations = enable_conversation_continuation

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
            prompt_tools=unknown,
            persistent_chat=(Feature(state="unknown", evidence="tested",
                                     note="opt-in experimental /c/<uuid> navigation; local fixtures only")
                             if self.supports_upstream_conversations else unsupported),
            projects=unknown, file_upload=unknown, file_download=unknown,
            interruption=unknown, reasoning_levels=[],
        )

    async def complete_in_session(
        self, model: str, messages: list[Message], *,
        upstream_conversation_id: str | None,
    ) -> ProviderOutput:
        """Experimental, one-click continuation; never replay ambiguous turns.

        The first turn is sent to a new chat and must yield a canonical URL.
        Subsequent turns navigate to that exact chat and send ONLY the new user
        message, because the upstream UI already retains prior turns.
        """
        if not self.supports_upstream_conversations:
            return await super().complete_in_session(
                model, messages, upstream_conversation_id=upstream_conversation_id,
            )
        if model != CHATGPT_MODEL:
            raise BrowserUIError("invalid_model", "unknown model")
        if not messages or messages[-1].role != "user" or not isinstance(messages[-1].content, str):
            raise BrowserUIError("unsupported_message", "last session turn must be plain user text")
        origin = _origin(self.site.url)
        target = (chatgpt_conversation_url(origin, upstream_conversation_id)
                  if upstream_conversation_id is not None else self.site.url)
        payload = (messages[-1].content if upstream_conversation_id is not None
                   else serialize_messages(messages))
        async with self._turn_lock:
            await self.browser.start()
            tab_id = "chat-" + uuid.uuid4().hex
            page = None
            submitted = False
            try:
                page = await self.browser.open_tab(tab_id, target)
                if _origin(page.url) != origin:
                    raise BrowserUIError("conversation_mismatch", "browser left expected site before submission")
                observed = await inspect_ui(page, self.site.selectors)
                if observed.state != "ready":
                    raise BrowserUIError(observed.state, "ChatGPT UI is not ready: " + observed.state)
                if upstream_conversation_id is not None:
                    current = parse_chatgpt_conversation_id(page.url, expected_origin=origin)
                    if current != upstream_conversation_id:
                        raise BrowserUIError("conversation_mismatch", "navigation did not preserve requested chat")
                    baseline = await self._capture_baseline(page)
                    # Refuse a blank/new page masquerading as the requested
                    # existing conversation; never blindly start a replacement.
                    if baseline.count < 1:
                        raise BrowserUIError("conversation_unverified", "existing conversation not visibly loaded")
                else:
                    if parse_chatgpt_conversation_id(page.url, expected_origin=origin) is not None:
                        raise BrowserUIError("unexpected_conversation", "new chat started at existing conversation")
                    baseline = await self._capture_baseline(page)
                await page.locator(self.site.selectors.composer).fill(payload)
                send = await self._wait_for_send_ready(page)
                if _origin(page.url) != origin or (upstream_conversation_id is not None and
                    parse_chatgpt_conversation_id(page.url, expected_origin=origin) != upstream_conversation_id):
                    raise BrowserUIError("conversation_mismatch", "conversation changed before submission")
                submitted = True  # even an ambiguous click may have reached the server
                await send.click()
                answer = await self._wait_for_answer(page, baseline, False)
                # SPA URL can lag behind completed DOM rendering. Bounded wait,
                # with no re-navigation or re-submission.
                deadline = time.monotonic() + min(3.0, self.site.timeout_s)
                conversation_id = None
                while time.monotonic() < deadline:
                    conversation_id = parse_chatgpt_conversation_id(page.url, expected_origin=origin)
                    if conversation_id is not None:
                        break
                    await asyncio.sleep(min(0.1, self.site.poll_s))
                if conversation_id is None or (upstream_conversation_id is not None
                                               and upstream_conversation_id != conversation_id):
                    raise BrowserUIError("remote_outcome_unknown", "conversation identity could not be verified")
                return ProviderOutput(content=answer, upstream_conversation_id=conversation_id)
            except BrowserUIError:
                raise
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                if submitted:
                    raise BrowserUIError("remote_outcome_unknown", "browser turn outcome may be unknown") from exc
                raise BrowserUIError("before_submit_failed", "browser failed before sending") from exc
            finally:
                if page is not None:
                    await self.browser.close_tab(tab_id)

    async def _capture_baseline(self, page: Any) -> TurnBaseline:
        return await snapshot_turns(page, self.site.selectors.assistant_message)

    async def _wait_for_answer(self, page: Any, baseline: TurnBaseline,
                               done_was_absent: bool) -> str:
        """Require new assistant text AND per-turn completion, not idle time."""
        del done_was_absent  # global copy buttons can belong to *old* turns
        deadline = time.monotonic() + self.site.timeout_s
        previous = None
        stable = 0
        active_key: str | None = None
        while time.monotonic() < deadline:
            state = await inspect_ui(page, self.site.selectors)
            if state.state in (
                "login_required", "security_challenge", "rate_limited",
                "browser_disconnected",
            ):
                raise BrowserUIError(state.state, "ChatGPT UI is not available: " + state.state)

            found = await find_new_turn(page, self.site.selectors.assistant_message, baseline)
            if found is not None:
                new_turn, ident = found
                # Pin text stability to one particular new answer. A changed
                # logical ID must reset the timer, not inherit an old answer's
                # stable observations.
                candidate_key = "id:" + ident if ident else "count-fallback"
                if candidate_key != active_key:
                    active_key, previous, stable = candidate_key, None, 0
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
            else:
                # A previously detected turn could be temporarily virtualized.
                # Do not return a response until its visible completion remains
                # established on the current DOM.
                active_key, previous, stable = None, None, 0
            await asyncio.sleep(self.site.poll_s)
        raise BrowserUIError(
            "generation_timeout",
            "ChatGPT did not provide verifiable completed-turn evidence; remote outcome unknown",
        )
