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
from ..browser.surface import observe_status, inspect_surface
from ..browser.challenge import inspect_human_verification
from ..browser.manager import BrowserManager, BrowserNavigationError, _origin
from ..capabilities import Capabilities, Feature
from ..models import Message, ProviderOutput
from .browser_ui import BrowserSiteProfile, BrowserUIError, BrowserUIProvider, serialize_messages
from .turn_identity import TurnBaseline, find_new_turn, snapshot_turns
from .reply_text import is_trustworthy_answer
from .chatgpt_page_state import chatgpt_page_error, chatgpt_feature_inventory
from .chatgpt_projects import (
    ChatGPTProject, parse_project_url, verify_chatgpt_project_destination,
)

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
        'button#composer-submit-button, '
        'button[aria-label="Send message"], '
        'button[aria-label="Send prompt"], '
        'button[aria-label*="Send" i]'
    ),
    assistant_message='[data-message-author-role="assistant"], main [data-conversation-role="assistant"]',
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
    human_verification_probe=True,
    rate_limited='[data-testid="rate-limit-error"]',
    generation_in_progress='button[data-testid="stop-button"]',
    response_complete='button[data-testid="copy-turn-action-button"]',
    send_button_may_appear_after_input=True,
)

# Only the *new* assistant turn's own enclosing article is considered complete.
# A copy button on an earlier turn cannot complete the current request.
TURN_COPY_BUTTON = (
    'button[data-testid="copy-turn-action-button"],'
    'button[aria-label="Copy"],'
    'button[aria-label="Copy message"],'
    'button[aria-label="Copy response"]'
)
TURN_PENDING = (
    '[aria-busy="true"], [class*="loading-shimmer"],'
    ' [class*="result-thinking"]'
)

# Read-only recovery is intentionally anchored to role-bearing turn markers,
# never to arbitrary page text or the sidebar.  Newer ChatGPT renderers may
# place the role on an article or a bubble rather than the message div.
RECOVERY_USER_SELECTORS = (
    '[data-message-author-role="user"]',
    'article[data-turn="user"]',
    '[data-testid^="conversation-turn-"][data-turn="user"]',
    'main [data-conversation-role="user"]',
    'main [data-user-message-bubble]',
)
RECOVERY_ASSISTANT_SELECTORS = (
    '[data-message-author-role="assistant"]',
    'article[data-turn="assistant"]',
    '[data-testid^="conversation-turn-"][data-turn="assistant"]',
    'main [data-conversation-role="assistant"]',
)
# Deliberately limited to count-only signals; do not return page text or IDs.
RECOVERY_DOM_MARKERS = {
    "user_role": '[data-message-author-role="user"]',
    "user_article": 'article[data-turn="user"]',
    "user_bubble": 'main [data-user-message-bubble]',
    "user_conversation_role": 'main [data-conversation-role="user"]',
    "assistant_role": '[data-message-author-role="assistant"]',
    "assistant_article": 'article[data-turn="assistant"]',
    "assistant_conversation_role": 'main [data-conversation-role="assistant"]',
    "assistant_start": 'main [data-chatgpt-agent-turn-start]',
    "turn_key": 'main [data-turn-key]',
    "conversation_articles": 'main [data-testid^="conversation-turn-"]',
    "copy_actions": TURN_COPY_BUTTON,
}


async def _recovery_last_marked_turn(page: Any, selectors: tuple[str, ...]) -> Any | None:
    """Select the last node from the most specific available role-bearing family.

    Selectors are ordered to prefer established message roles.  Never fall
    back to a text-only search that could match sidebar or unrelated content.
    """
    for css in selectors:
        matches = page.locator(css)
        if await matches.count():
            return matches.last
    return None


async def _recovery_user_text(turn: Any) -> str:
    """Read only the user bubble, excluding adjacent Edit/action controls."""
    for css in ('[data-user-message-bubble]', '.whitespace-pre-wrap', '.user-message-bubble-color'):
        content = turn.locator(css)
        if await content.count():
            return ' '.join((await content.first.inner_text()).split())
    return ' '.join((await turn.inner_text()).split())


# A few UI variants render the answer as plain text after a separate
# [data-chatgpt-agent-turn-start] sentinel, rather than inside .markdown.
# This walk is STRICTLY limited to the assistant's own keyed/role-bearing turn:
# it never scans main, the sidebar, earlier users or unrelated action labels.
_SCOPED_PLAIN_ANSWER_JS = r"""(role) => {
    const after = (a, b) => !!(a && b &&
        (a.compareDocumentPosition(b) & Node.DOCUMENT_POSITION_FOLLOWING));
    const scope = role.closest('[data-turn-key]') ||
        role.closest('article[data-turn="assistant"]') ||
        role.closest('[data-testid^="conversation-turn-"]');
    const data = {scope: !!scope, startCount: 0, copyAfterStart: 0,
        textNodes: 0, answer: '', reason: 'no_scoped_turn'};
    if (!scope || !scope.contains(role)) return data;
    const starts = [...scope.querySelectorAll('[data-chatgpt-agent-turn-start]')]
        .filter(e => after(role, e));
    data.startCount = starts.length;
    if (starts.length !== 1) {data.reason = 'start_not_unique'; return data;}
    const start = starts[0];
    const copies = [...scope.querySelectorAll(
        'button[data-testid="copy-turn-action-button"], button[aria-label="Copy"],'+
        'button[aria-label="Copy message"], button[aria-label="Copy response"]')]
        .filter(e => after(start, e));
    data.copyAfterStart = copies.length;
    if (!copies.length) {data.reason = 'no_scoped_copy'; return data;}
    const end = copies[0];
    const walker = document.createTreeWalker(scope, NodeFilter.SHOW_TEXT);
    const pieces = [];
    let node;
    while ((node = walker.nextNode())) {
        if (!after(start, node) || !after(node, end)) continue;
        const element = node.parentElement;
        if (!element || !element.getClientRects().length || element.closest(
            'button, a, input, textarea, select, [role="button"],'+
            '[contenteditable="true"], [aria-hidden="true"],'+
            '[data-conversation-role], script, style, noscript, .sr-only, [class*="sr-only"]')) continue;
        const value = (node.textContent || '').trim();
        if (!value) continue;
        data.textNodes++;
        if (pieces.length < 128) pieces.push(value);
    }
    data.answer = pieces.join('\n').slice(0, 262145);
    data.reason = data.answer ? 'plain_candidate' : 'no_plain_candidate';
    return data;
}"""


async def _scoped_plain_answer(assistant: Any) -> dict[str, Any]:
    """Read text only between assistant-start and its own Copy action."""
    # Test doubles and older browser backends may not implement evaluate.
    # Missing DOM-evaluation support is NOT evidence of answer completion.
    if not callable(getattr(assistant, 'evaluate', None)):
        return {'reason': 'scoped_dom_unavailable', 'answer': ''}
    return await assistant.evaluate(_SCOPED_PLAIN_ANSWER_JS)


async def _recovery_assistant_text(assistant: Any) -> str:
    """Extract *answer content*, not the assistant's accessibility role label.

    A newer ChatGPT renderer can use [data-conversation-role=assistant]
    solely as a spoken label ("ChatGPT said:") while placing the actual
    markdown as a sibling inside the same logical [data-turn-key] container.
    Only inspect visible, answer-shaped nodes belonging to that SAME turn,
    and only when they are inside or DOM-after the assistant role marker.
    Never use a page-wide markdown search or a sibling from another turn.
    """
    selector = '.markdown, [data-message-content], [data-testid="assistant-message-content"]'
    candidate_texts: list[str] = []

    async def collect(nodes: Any, *, reference: Any | None = None) -> None:
        for index in range(min(32, await nodes.count())):
            node = nodes.nth(index)
            if reference is not None:
                # A markdown block belonging to the earlier user prompt, even
                # in the same group, must never become the assistant answer.
                handle = await node.element_handle()
                ref_handle = await reference.element_handle()
                follows = await node.evaluate("""(node, role) =>
                    !!(node && role && (role.contains(node) ||
                    (role.compareDocumentPosition(node) & Node.DOCUMENT_POSITION_FOLLOWING)))
                """, ref_handle)
                if not follows:
                    continue
            if not await node.is_visible():
                continue
            text = (await node.inner_text()).strip()
            if is_trustworthy_answer(text) and text not in candidate_texts:
                candidate_texts.append(text)

    await collect(assistant.locator(selector))
    if not candidate_texts:
        grouped = assistant.locator('xpath=ancestor::*[@data-turn-key][1]')
        if await grouped.count():
            await collect(grouped.first.locator(selector), reference=assistant)
    if len(candidate_texts) == 1:
        return candidate_texts[0]
    if len(candidate_texts) > 1:
        raise BrowserUIError('reconcile_assistant_content_ambiguous',
                             'Multiple competing assistant content blocks')

    # Simple role-marked fixtures (and some older UIs) put the visible answer
    # directly in the role node, without a .markdown wrapper. Avoid a DOM
    # fallback when an explicit role node already contains a trustworthy reply.
    raw = (await assistant.inner_text()).strip()
    if is_trustworthy_answer(raw) and not raw.casefold().startswith(("chatgpt said:", "chatgpt says:", "assistant said:", "assistant says:")):
        return raw

    # The live page may not contain .markdown at all. Only then try a bounded
    # answer walk with a start marker and a later Copy action inside the same
    # dedicated turn; exclude buttons, hidden nodes and content after Copy.
    plain = await _scoped_plain_answer(assistant)
    if plain["reason"] == "plain_candidate" and is_trustworthy_answer(plain["answer"]):
        return plain["answer"]
    raise BrowserUIError('reconcile_assistant_content_unverified',
                         'Assistant content could not be distinguished from a role label')


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


def _project_segment_matches(segment: str, project_id: str) -> bool:
    """Accept exactly the verified ID, optionally followed by a UI slug.

    ChatGPT's public Project chats can use ``g-p-<id>-project-name`` even
    though Project Home and sidebar identity use ``g-p-<id>``. Never infer a
    different project from a shared prefix, an arbitrary URL, or a slug alone.
    """
    import re
    if segment == project_id:
        return True
    prefix = project_id + "-"
    if not segment.startswith(prefix):
        return False
    slug = segment[len(prefix):]
    return bool(re.fullmatch(r"[a-z0-9]+(?:-[a-z0-9]+)*", slug) and len(slug) <= 100)


def parse_project_conversation_id(url: str, project_id: str, *,
                                  expected_origin: str = "https://chatgpt.com") -> str | None:
    """Accept only same-project /g/<id>[-slug]/c/<canonical uuid> routes."""
    try:
        parts = urlsplit(url)
        if _origin(url) != expected_origin or parts.query or parts.fragment:
            return None
        path = parts.path.split("/")
        if (len(path) != 5 or path[1] != "g" or
            not _project_segment_matches(path[2], project_id) or path[3] != "c"):
            return None
        if parse_project_url(f"{expected_origin}/g/{project_id}/project", expected_origin=expected_origin) != project_id:
            return None
        identifier = path[4]
        return identifier if str(uuid.UUID(identifier)) == identifier else None
    except (ValueError, TypeError, AttributeError, BrowserNavigationError):
        return None


def project_conversation_url(origin: str, project_id: str, conversation_id: str) -> str:
    """Construct only a validated, same-Project continuation URL."""
    url = f"{origin}/g/{project_id}/c/{conversation_id}"
    if parse_project_conversation_id(url, project_id, expected_origin=origin) != conversation_id:
        raise BrowserUIError("invalid_conversation_id", "invalid Project chat identity")
    return url


class ChatGPTWebProvider(BrowserUIProvider):
    """One-shot selected-model chat, with one request at a time per account."""

    def __init__(self, browser: BrowserManager, *, timeout_s: float = 120.0,
                 poll_s: float = 0.25, stable_observations: int = 4,
                 url: str = CHATGPT_URL, selectors: UISelectors = CHATGPT_SELECTORS,
                 enable_conversation_continuation: bool = False,
                 enable_project_messages: bool = False):
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
        if enable_project_messages and not enable_conversation_continuation:
            raise ValueError("Project messages require upstream conversation continuation")
        self.supports_upstream_conversations = enable_conversation_continuation
        self.supports_project_messages = enable_project_messages

    @staticmethod
    async def _check_page_error(page: Any, *, wait_seconds: float = 0.0) -> None:
        """Recognize inaccessible chat, even during a delayed SPA error redirect.

        The bounded wait is used only when routing already failed; it never
        navigates again or retries a model request.
        """
        deadline = time.monotonic() + wait_seconds
        while True:
            if await inspect_human_verification(page):
                raise BrowserUIError("security_challenge", "Human verification required; no request replay")
            code = await chatgpt_page_error(page)
            if code is not None:
                raise BrowserUIError(code, "ChatGPT conversation unavailable; no request replay")
            if time.monotonic() >= deadline:
                return
            await asyncio.sleep(min(0.1, deadline - time.monotonic()))

    async def inspect_feature_controls(self, *, wait_seconds: float = 12.0) -> dict[str, Any]:
        """Wait for SPA hydration, then inventory optional controls without clicking.

        ``DOMContentLoaded`` is not enough for the React UI. The wait is bounded
        to 30 seconds, respects visible login/challenge states, and never treats
        an absent model/effort control as proof a feature is unsupported.
        """
        if not 0 <= wait_seconds <= 30:
            raise ValueError("feature inventory wait interval must be 0-30 seconds")
        async with self._turn_lock:
            await self.browser.start()
            tab_id = "feature-scan-" + uuid.uuid4().hex
            page = None
            try:
                page = await self.browser.open_tab(tab_id, self.site.url)
                started = time.monotonic()
                observed, _status_controls, _elapsed = await observe_status(
                    page, self.site.selectors, wait_seconds=wait_seconds,
                )
                if observed.state == "security_challenge":
                    # Controls are inaccessible, not missing, on a human
                    # verification page. Never inspect menus or interact.
                    controls = {key: "unavailable" for key in (
                        "model_picker", "effort_picker", "attachment_button",
                        "file_input", "voice_button", "tools_menu",
                    )}
                else:
                    controls = await chatgpt_feature_inventory(page)
                # Optional controls may mount after the composer. A stable
                # READY state alone cannot establish their absence. If no
                # optional control has appeared, use the remaining budget;
                # otherwise wait for a settled snapshot (with a short grace).
                if observed.state == "ready":
                    earliest = time.monotonic() + min(1.0, max(0.0, wait_seconds - (time.monotonic() - started)))
                    last_change = time.monotonic()
                    while time.monotonic() - started < wait_seconds:
                        await asyncio.sleep(min(0.25, max(0.0, wait_seconds - (time.monotonic() - started))))
                        new_controls = await chatgpt_feature_inventory(page)
                        if new_controls != controls:
                            controls = new_controls
                            last_change = time.monotonic()
                        if (any(v == "detected" for v in controls.values()) and
                                time.monotonic() >= earliest and
                                time.monotonic() - last_change >= 0.75):
                            break
                on_chatgpt_domain = urlsplit(page.url).hostname == urlsplit(self.site.url).hostname
                return {"page_state": observed.state,
                        "ui_evidence": observed.evidence,
                        "waited_seconds": round(time.monotonic() - started, 2),
                        "on_expected_domain": on_chatgpt_domain,
                        "surface": await inspect_surface(page),
                        "controls": controls,
                        "selection_changed": False, "uploaded": False,
                        "submitted": False}
            finally:
                if page is not None:
                    await self.browser.close_tab(tab_id)

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
            projects=(Feature(state="unknown", evidence="tested",
                              note="opt-in Project routing; fixture-only verification")
                      if self.supports_project_messages else unknown),
            file_upload=unknown, file_download=unknown,
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
                await self._fill_composer(page, payload)
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

    async def complete_in_project_session(
        self, model: str, messages: list[Message], *, project_id: str,
        project_name: str, upstream_conversation_id: str | None,
    ) -> ProviderOutput:
        """Explicitly opted-in, fail-closed Project-native chat routing.

        Every first turn begins on the independently verified Project Home.
        Every continued turn must load an existing /g/<project>/c/<uuid>
        conversation with visible history. No fallback to ordinary /c/<uuid>.
        """
        if not self.supports_project_messages or not self.supports_upstream_conversations:
            raise BrowserUIError("project_messages_disabled", "Project-scoped submission is disabled")
        if model != CHATGPT_MODEL:
            raise BrowserUIError("invalid_model", "unknown model")
        if not messages or messages[-1].role != "user" or not isinstance(messages[-1].content, str):
            raise BrowserUIError("unsupported_message", "last Project turn must be plain user text")
        origin = _origin(self.site.url)
        home = f"{origin}/g/{project_id}/project"
        if parse_project_url(home, expected_origin=origin) != project_id or not project_name or project_name == project_id:
            raise BrowserUIError("invalid_project", "Project binding must be verified first")
        continuing = upstream_conversation_id is not None
        target = (project_conversation_url(origin, project_id, upstream_conversation_id)
                  if continuing else home)
        # A newly bound Project session starts empty. Keep the prompt native to
        # the Project instead of injecting role-serialized JSON into its editor.
        payload = messages[-1].content
        async with self._turn_lock:
            await self.browser.start()
            tab_id = "project-chat-" + uuid.uuid4().hex
            page = None
            submitted = False
            try:
                page = await self.browser.open_tab(tab_id, target)
                await self._check_page_error(page)
                if continuing:
                    if parse_project_conversation_id(page.url, project_id, expected_origin=origin) != upstream_conversation_id:
                        await self._check_page_error(page, wait_seconds=0.65)
                        raise BrowserUIError("project_mismatch", "Project conversation redirect was not trusted")
                    baseline = await self._capture_baseline(page)
                    if baseline.count < 1:
                        raise BrowserUIError("conversation_unverified", "prior Project conversation is not visibly loaded")
                else:
                    result = await verify_chatgpt_project_destination(
                        page, ChatGPTProject(project_id, project_name, home),
                        origin=origin, wait_seconds=min(8.0, self.site.timeout_s),
                    )
                    if result.state != "verified":
                        raise BrowserUIError("project_unverified", "Project Home identity was not verified")
                    baseline = await self._capture_baseline(page)
                observed = await inspect_ui(page, self.site.selectors)
                if observed.state != "ready":
                    raise BrowserUIError(observed.state, "Project chat UI is not ready")
                await self._fill_composer(page, payload)
                send = await self._wait_for_send_ready(page)
                if ((continuing and parse_project_conversation_id(
                        page.url, project_id, expected_origin=origin) != upstream_conversation_id)
                    or (not continuing and parse_project_url(page.url, expected_origin=origin) != project_id)):
                    raise BrowserUIError("project_mismatch", "Project changed before submission")
                submitted = True  # click outcome is ambiguous until confirmed
                await send.click()
                answer = await self._wait_for_answer(page, baseline, False)
                deadline = time.monotonic() + min(3.0, self.site.timeout_s)
                chat_id = None
                while time.monotonic() < deadline:
                    chat_id = parse_project_conversation_id(
                        page.url, project_id, expected_origin=origin,
                    )
                    if chat_id is not None:
                        break
                    await asyncio.sleep(min(.1, self.site.poll_s))
                if chat_id is None or (continuing and chat_id != upstream_conversation_id):
                    raise BrowserUIError("remote_outcome_unknown", "Project conversation identity not confirmed")
                return ProviderOutput(content=answer, upstream_conversation_id=chat_id)
            except BrowserUIError as exc:
                if submitted and exc.code not in ("remote_outcome_unknown",):
                    raise BrowserUIError("remote_outcome_unknown", "Project request may have been submitted") from exc
                raise
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                if submitted:
                    raise BrowserUIError("remote_outcome_unknown", "Project request may have been submitted") from exc
                raise BrowserUIError("before_submit_failed", "Project browser failed before submission") from exc
            finally:
                if page is not None:
                    await self.browser.close_tab(tab_id)

    async def inspect_project_repair_dom(
        self, *, project_id: str, conversation_url: str, user_message: str,
    ) -> dict[str, Any]:
        """Read-only answer-shape inspection for an exact, saved Project turn.

        The API separately validates the immutable request and Project binding.
        Never return page text, URL, IDs, attributes, or screenshots.
        """
        if not (self.supports_project_messages and self.supports_upstream_conversations):
            raise BrowserUIError('project_messages_disabled', 'Project inspection is disabled')
        origin = _origin(self.site.url)
        chat_id = parse_project_conversation_id(
            conversation_url, project_id, expected_origin=origin)
        if chat_id is None or not isinstance(user_message, str) or not user_message:
            raise BrowserUIError('reconcile_invalid_input', 'Invalid Project inspection input')
        async with self._turn_lock:
            await self.browser.start()
            tab_id = 'repair-dom-' + uuid.uuid4().hex
            page = None
            try:
                page = await self.browser.open_tab(tab_id, conversation_url)
                await self._check_page_error(page)
                deadline = time.monotonic() + min(8.0, self.site.timeout_s)
                last = None
                while time.monotonic() < deadline:
                    await self._check_page_error(page)
                    if parse_project_conversation_id(
                        page.url, project_id, expected_origin=origin) != chat_id:
                        await self._check_page_error(page, wait_seconds=0.65)
                        raise BrowserUIError('reconcile_route_mismatch', 'Project route changed')
                    user = await _recovery_last_marked_turn(page, RECOVERY_USER_SELECTORS)
                    assistant = await _recovery_last_marked_turn(page, RECOVERY_ASSISTANT_SELECTORS)
                    if user is not None and assistant is not None:
                        user_matches = (await _recovery_user_text(user) ==
                                        ' '.join(user_message.split()))
                        uh, ah = await user.element_handle(), await assistant.element_handle()
                        ordered = await page.evaluate("""(els) => !!(els[0] && els[1] &&
                            (els[0].compareDocumentPosition(els[1]) & Node.DOCUMENT_POSITION_FOLLOWING))""", [uh, ah])
                        plain = await _scoped_plain_answer(assistant) if ordered and user_matches else {}
                        named = assistant.locator(
                            '.markdown, [data-message-content], [data-testid="assistant-message-content"]')
                        group = assistant.locator('xpath=ancestor::*[@data-turn-key][1]')
                        group_named = (group.first.locator(
                            '.markdown, [data-message-content], [data-testid="assistant-message-content"]')
                            if await group.count() else None)
                        return {
                            'route_verified': True, 'user_matches': user_matches,
                            'assistant_after_user': bool(ordered),
                            'assistant_turn_key': bool(await group.count()),
                            'named_content_inside_role': min(99, await named.count()),
                            'named_content_in_turn_key': (
                                min(99, await group_named.count()) if group_named is not None else 0),
                            'assistant_start_after_role': min(99, plain.get('startCount', 0)),
                            'copy_after_start': min(99, plain.get('copyAfterStart', 0)),
                            'plain_text_nodes': min(99, plain.get('textNodes', 0)),
                            'scoped_plain_status': plain.get('reason', 'not_checked'),
                            'scoped_answer_usable': bool(
                                plain.get('answer') and is_trustworthy_answer(plain['answer'])),
                            'submitted': False, 'journal_changed': False,
                        }
                    last = 'turns_not_loaded'
                    await asyncio.sleep(self.site.poll_s)
                raise BrowserUIError('reconcile_turns_missing', last or 'Turns not found')
            except BrowserUIError:
                raise
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                raise BrowserUIError('reconcile_dom_unavailable', 'Answer shape could not be inspected') from exc
            finally:
                if page is not None:
                    await self.browser.close_tab(tab_id)

    async def inspect_project_conversation_dom(
        self, *, project_id: str, conversation_url: str,
    ) -> dict[str, Any]:
        """Read-only marker census for a manually supplied same-Project chat.

        No prompt text, DOM HTML, tokens, cookies, URLs, IDs or message bodies
        appear in the result. No Send interactions or local DB modifications.
        """
        if not (self.supports_project_messages and self.supports_upstream_conversations):
            raise BrowserUIError("project_messages_disabled", "Project inspection is disabled")
        origin = _origin(self.site.url)
        expected_chat = parse_project_conversation_id(
            conversation_url, project_id, expected_origin=origin)
        if expected_chat is None:
            raise BrowserUIError("reconcile_invalid_input", "Invalid Project conversation route")
        async with self._turn_lock:
            await self.browser.start()
            tab_id = "project-dom-inspect-" + uuid.uuid4().hex
            page = None
            try:
                page = await self.browser.open_tab(tab_id, conversation_url)
                await self._check_page_error(page)
                deadline = time.monotonic() + min(8.0, self.site.timeout_s)
                counts: dict[str, int] = {}
                previous = None
                stable = 0
                while True:
                    await self._check_page_error(page)
                    if parse_project_conversation_id(page.url, project_id, expected_origin=origin) != expected_chat:
                        await self._check_page_error(page, wait_seconds=0.65)
                        raise BrowserUIError("reconcile_route_mismatch", "Project conversation navigation changed")
                    state = await inspect_ui(page, self.site.selectors)
                    if state.state in ("login_required", "security_challenge", "rate_limited", "browser_disconnected"):
                        raise BrowserUIError("reconcile_page_unavailable", "Project page unavailable")
                    counts = {name: min(999, await page.locator(css).count())
                              for name, css in RECOVERY_DOM_MARKERS.items()}
                    if counts == previous:
                        stable += 1
                    else:
                        stable, previous = 1, counts.copy()
                    if stable >= 3 or time.monotonic() >= deadline:
                        break
                    await asyncio.sleep(self.site.poll_s)
                return {"route_verified": True, "markers": counts,
                        "submitted": False, "journal_changed": False}
            except BrowserUIError:
                raise
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                raise BrowserUIError("reconcile_dom_unavailable", "Project DOM could not be inspected") from exc
            finally:
                if page is not None:
                    await self.browser.close_tab(tab_id)

    async def reconcile_project_turn(
        self, *, project_id: str, project_name: str,
        conversation_url: str, user_message: str,
    ) -> ProviderOutput:
        """Read-only, fail-closed verification of an already submitted turn.

        A recovery is *not* a new request. This method never types, clicks or
        stores any data. Only the API, after checking the unknown journal turn,
        may atomically commit a verified reply. Error codes are finite and
        never include DOM text, credentials or remote URLs.
        """
        if not self.supports_project_messages or not self.supports_upstream_conversations:
            raise BrowserUIError("project_messages_disabled", "Project reconciliation is disabled")
        origin = _origin(self.site.url)
        expected_chat = parse_project_conversation_id(
            conversation_url, project_id, expected_origin=origin,
        )
        if expected_chat is None or not project_name or not isinstance(user_message, str) or not user_message:
            raise BrowserUIError("reconcile_invalid_input", "Invalid Project recovery parameters")
        async with self._turn_lock:
            await self.browser.start()
            tab_id = "project-reconcile-" + uuid.uuid4().hex
            page = None
            # The last observed stage is deliberately non-sensitive and helps
            # distinguish wrong selectors from actual missing completion.
            stage = "page_not_loaded"
            try:
                page = await self.browser.open_tab(tab_id, conversation_url)
                await self._check_page_error(page)
                previous, stable = None, 0
                deadline = time.monotonic() + min(12.0, self.site.timeout_s)
                while time.monotonic() < deadline:
                    await self._check_page_error(page)
                    if (parse_project_conversation_id(page.url, project_id, expected_origin=origin)
                            != expected_chat):
                        await self._check_page_error(page, wait_seconds=0.65)
                        raise BrowserUIError("reconcile_route_mismatch", "Project conversation navigation changed")
                    state = await inspect_ui(page, self.site.selectors)
                    if state.state in ("login_required", "security_challenge", "rate_limited", "browser_disconnected"):
                        raise BrowserUIError("reconcile_page_unavailable", "Project page unavailable")
                    user = await _recovery_last_marked_turn(page, RECOVERY_USER_SELECTORS)
                    assistant = await _recovery_last_marked_turn(page, RECOVERY_ASSISTANT_SELECTORS)
                    if user is None:
                        stage = "user_turn_missing"
                    elif assistant is None:
                        stage = "assistant_turn_missing"
                    else:
                        # The user message must be the *last marked* user turn,
                        # with exact text, and followed by a completed answer.
                        # An older identical prompt is not sufficient proof.
                        expected_text = " ".join(user_message.split())
                        actual_text = await _recovery_user_text(user)
                        if actual_text != expected_text:
                            stage = "user_text_mismatch"
                        else:
                            u_handle = await user.element_handle()
                            a_handle = await assistant.element_handle()
                            follows = await page.evaluate("""(args) => {
                                const u = args[0], a = args[1];
                                return !!(u && a && (u.compareDocumentPosition(a) & Node.DOCUMENT_POSITION_FOLLOWING));
                            }""", [u_handle, a_handle])
                            if not follows:
                                stage = "assistant_not_after_user"
                            else:
                                copy_ready = await self._turn_copy_ready(assistant)
                                pending = await assistant.locator(TURN_PENDING).count() > 0
                                try:
                                    answer = await _recovery_assistant_text(assistant)
                                except BrowserUIError as extraction_error:
                                    stage = extraction_error.code.removeprefix("reconcile_")
                                    previous, stable = None, 0
                                    answer = ""
                                if answer:
                                    if answer == previous:
                                        stable += 1
                                    else:
                                        previous, stable = answer, 1
                                    if pending or state.state == "generation_in_progress":
                                        stage = "assistant_still_generating"
                                    elif not copy_ready:
                                        stage = "completion_marker_missing"
                                    elif stable < self.site.stable_observations:
                                        stage = "reply_not_stable"
                                    else:
                                        return ProviderOutput(content=answer, upstream_conversation_id=expected_chat)
                    await asyncio.sleep(self.site.poll_s)
                raise BrowserUIError("reconcile_" + stage, "Project reply not verifiable: " + stage)
            except BrowserUIError:
                raise
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                raise BrowserUIError("reconcile_dom_unavailable", "Project DOM could not be inspected") from exc
            finally:
                if page is not None:
                    await self.browser.close_tab(tab_id)

    async def verify_project(self, project_id: str, *, wait_seconds: float = 8.0,
                             max_steps: int = 30):
        """Read-only, explicit UI verification for API Project binding.

        Identifies the chosen Project from the sidebar, opens its overview
        in a separate owned tab and requires a matching route and main heading.
        Never writes to the webpage or submits a message.
        """
        from .chatgpt_projects import (
            parse_project_url, scan_chatgpt_projects,
            verify_chatgpt_project_destination,
        )
        origin = _origin(self.site.url)
        if parse_project_url(f"{origin}/g/{project_id}/project", expected_origin=origin) != project_id:
            raise ValueError("invalid Project identifier")
        async with self._turn_lock:
            await self.browser.start()
            scan_id = "project-scan-" + uuid.uuid4().hex
            home = await self.browser.open_tab(scan_id, self.site.url)
            try:
                report = await scan_chatgpt_projects(
                    home, origin=origin, wait_seconds=wait_seconds, max_steps=max_steps,
                )
            finally:
                await self.browser.close_tab(scan_id)
            project = next((p for p in report.projects if p.id == project_id), None)
            if project is None:
                return None
            destination_id = "project-check-" + uuid.uuid4().hex
            page = await self.browser.open_tab(destination_id, project.url)
            try:
                return await verify_chatgpt_project_destination(
                    page, project, origin=origin, wait_seconds=wait_seconds,
                )
            finally:
                await self.browser.close_tab(destination_id)

    async def _visible_composer(self, page: Any) -> Any:
        """Choose one visible editor; hidden responsive duplicates are ignored."""
        nodes = page.locator(self.site.selectors.composer)
        visible = []
        for i in range(min(32, await nodes.count())):
            candidate = nodes.nth(i)
            if await candidate.is_visible():
                visible.append(candidate)
        if len(visible) != 1:
            raise BrowserUIError("ui_changed", "expected exactly one visible message composer")
        return visible[0]

    async def _fill_composer(self, page: Any, payload: str) -> None:
        composer = await self._visible_composer(page)
        await composer.fill(payload)

    async def _send_candidates(self, page: Any) -> list[Any]:
        """Only consider Send in the active composer's form when one exists.

        If a synthetic/older page has no form, use precise, named controls
        (never an arbitrary page-wide submit, Voice or attachment button).
        """
        composer = await self._visible_composer(page)
        form = composer.locator("xpath=ancestor::form[1]")
        scope = form if await form.count() else page
        primary = scope.locator(self.site.selectors.send_button)
        primary_count = await primary.count()
        if primary_count:
            return [primary.nth(i) for i in range(min(32, primary_count))]
        if await form.count():
            # Some localized icon-only sends lack recognized English labels.
            # Allow an unnamed submit only if it is the SOLE submit control
            # in the active form. Never guess among multiple candidates.
            submit = scope.locator('button[type="submit"]')
            if await submit.count() == 1:
                return [submit.first]
        return []

    async def _wait_for_send_ready(self, page: Any) -> Any:
        """Resolve a visible enabled send control after typing, no fallback Enter.

        Ambiguous errors are not retried because an Enter keypress may submit
        a message rather than simply newline-edit the composer.
        """
        deadline = time.monotonic() + min(5.0, self.site.timeout_s)
        while True:
            state = await inspect_ui(page, self.site.selectors)
            if state.state in (
                "login_required", "security_challenge", "rate_limited",
                "generation_in_progress", "browser_disconnected",
            ):
                raise BrowserUIError(state.state, "browser UI unavailable before submission: " + state.state)
            for button in await self._send_candidates(page):
                enabled = getattr(button, "is_enabled", None)
                if not await button.is_visible() or (callable(enabled) and not await enabled()):
                    continue
                # A type=submit control in the active form can be icon-only;
                # reject obvious microphone/attachment actions even if
                # mistakenly coded as submits by a page revision.
                get_attr = getattr(button, "get_attribute", None)
                label = ((await get_attr("aria-label")) or "").lower() if callable(get_attr) else ""
                if any(word in label for word in (
                    "dictat", "microphone", "voice", "record", "upload",
                    "attach", "cancel", "stop", "search", "audio",
                )):
                    continue
                if callable(get_attr) and (await get_attr("aria-disabled") or "").lower() == "true":
                    continue
                return button
            if time.monotonic() >= deadline:
                raise BrowserUIError(
                    "submit_unavailable", "no visible, enabled Send control after typing; nothing submitted",
                )
            await asyncio.sleep(min(self.site.poll_s, 0.2))

    async def check_composer_send(self, *, draft: str = "OpenAgent draft-only send check") -> dict[str, Any]:
        """Operator-initiated preflight: type an inert draft, NEVER click Send.

        A dedicated tab is closed afterward. No draft/account text or raw
        browser URL is logged. The page may transiently autosave the draft.
        """
        async with self._turn_lock:
            await self.browser.start()
            tab_id = "send-preflight-" + uuid.uuid4().hex
            try:
                page = await self.browser.open_tab(tab_id, self.site.url)
                state = await inspect_ui(page, self.site.selectors)
                if state.state != "ready":
                    return {"state": state.state, "submitted": False}
                await self._fill_composer(page, draft)
                try:
                    await self._wait_for_send_ready(page)
                    return {"state": "send_ready", "submitted": False}
                except BrowserUIError as exc:
                    return {"state": exc.code, "submitted": False}
            finally:
                await self.browser.close_tab(tab_id)

    async def _turn_copy_ready(self, assistant: Any) -> bool:
        """Completion must belong to this assistant turn, not prior history.

        Current layouts can put the action toolbar under the article, under a
        wider conversation-turn container, or inside the assistant node.
        Never accept a global page-wide Copy action as completion evidence.
        """
        scopes = (
            assistant.locator("xpath=ancestor::article[1]"),
            assistant.locator('xpath=ancestor::*[starts-with(@data-testid, "conversation-turn-")][1]'),
            assistant,
        )
        for scope in scopes:
            if not await scope.count():
                continue
            controls = scope.locator(TURN_COPY_BUTTON)
            # The toolbar may be CSS-hidden until hover; existence is enough
            # ONLY inside the assistant turn itself or its dedicated wrapper.
            if await controls.count() or await controls.first.is_visible():
                return True
        # New UI: a turn-key wrapper can group a user's prompt and its answer.
        # Its Copy action is valid only when DOM-order AFTER this assistant,
        # not merely because an earlier/user Copy action is present.
        group = assistant.locator('xpath=ancestor::*[@data-turn-key][1]')
        if await group.count():
            controls = group.locator(TURN_COPY_BUTTON)
            for index in range(min(32, await controls.count())):
                action = controls.nth(index)
                answer_handle = await assistant.element_handle()
                if await action.evaluate("""(b, a) =>
                    !!(a && b && (a.compareDocumentPosition(b) & Node.DOCUMENT_POSITION_FOLLOWING))
                """, answer_handle):
                    return True
        return False

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
            await self._check_page_error(page)
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
                copy_ready = await self._turn_copy_ready(new_turn)
                pending = await new_turn.locator(TURN_PENDING).count() > 0
                rendering = state.state == "generation_in_progress"
                try:
                    text = await _recovery_assistant_text(new_turn)
                except BrowserUIError as extraction_error:
                    # An accessibility-only heading is not an answer. Keep
                    # waiting for real content, but never return the heading.
                    if extraction_error.code not in (
                        "reconcile_assistant_content_unverified",
                        "reconcile_assistant_content_ambiguous",
                    ):
                        raise
                    text = ""
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
