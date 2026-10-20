"""Small DOM-based browser provider for fixture testing and permitted websites.

This intentionally does not target any named commercial service. The caller
supplies a verified site profile, including its selectors and model identifier.
Messages are transmitted as an ordered JSON envelope rather than silently
losing system/tool turns. The bridge NEVER executes model-generated commands.
"""
from __future__ import annotations

import asyncio
import json
import time
import uuid
from dataclasses import dataclass
from typing import Any

from ..browser.diagnostics import UIObservation, UISelectors, inspect_ui
from ..browser.manager import BrowserDisconnected, BrowserManager, BrowserSessionError
from ..capabilities import Capabilities, Feature
from ..models import Message, ProviderOutput
from .base import Provider


class BrowserUIError(RuntimeError):
    """A categorized, content-free user-visible browser provider error."""

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


@dataclass(frozen=True)
class BrowserSiteProfile:
    provider_id: str
    model_id: str
    url: str
    selectors: UISelectors
    timeout_s: float = 30.0
    poll_s: float = 0.15
    stable_observations: int = 3

    def __post_init__(self) -> None:
        if not self.provider_id or not self.model_id.startswith(self.provider_id + "/"):
            raise ValueError("model_id must start with provider_id/")
        if not (1 <= self.timeout_s <= 300):
            raise ValueError("timeout_s must be between 1 and 300")
        if not (0.02 <= self.poll_s <= 2.0):
            raise ValueError("poll_s out of range")
        if not (2 <= self.stable_observations <= 20):
            raise ValueError("stable_observations out of range")
        from ..browser.manager import _origin
        _origin(self.url)


def serialize_messages(messages: list[Message]) -> str:
    """Explicitly preserve all roles and tool results, in compact JSON.

    These are serialized as regular browser prompt text, NOT provider-native
    roles. No claim is made of equivalent instruction priority.
    """
    content = [m.model_dump(exclude_none=True, mode="json") for m in messages]
    return "OpenAgent ordered conversation (JSON):\n" + json.dumps(
        content, separators=(",", ":"), ensure_ascii=False,
    ) + "\nRespond to the final user request."


class BrowserUIProvider(Provider):
    """One-shot DOM adapter. Does not persist an upstream conversation yet."""

    def __init__(self, site: BrowserSiteProfile, browser: BrowserManager):
        self.id = site.provider_id
        self.site = site
        self.browser = browser
        from ..browser.manager import _origin
        if browser.config.allowed_origins and _origin(site.url) not in browser.config.allowed_origins:
            raise ValueError("site URL is not in the browser allowlist")

    async def models(self) -> list[str]:
        return [self.site.model_id]

    async def capabilities(self, model: str) -> Capabilities:
        if model != self.site.model_id:
            raise ValueError("unknown model")
        no = Feature(state="unsupported", evidence="declared")
        maybe = Feature(state="unknown", note="requires provider-specific verification")
        return Capabilities(
            provider=self.id, model=model, transport="browser",
            authentication="browser-session; UI readiness only",
            streaming=no, native_tools=no, prompt_tools=maybe,
            persistent_chat=no, projects=maybe, file_upload=maybe,
            file_download=maybe, interruption=maybe,
        )

    async def inspect(self) -> UIObservation:
        if self.browser.status().state != "running":
            return UIObservation("browser_disconnected", "browser_not_running")
        tab_id = "diag-" + uuid.uuid4().hex
        try:
            page = await self.browser.open_tab(tab_id, self.site.url)
            return await inspect_ui(page, self.site.selectors)
        except (BrowserDisconnected, BrowserSessionError):
            return UIObservation("browser_disconnected", "transport_unavailable")
        finally:
            await self.browser.close_tab(tab_id)

    async def complete(
        self, model: str, messages: list[Message], tools: list[dict] | None = None,
    ) -> ProviderOutput:
        if model != self.site.model_id:
            raise BrowserUIError("invalid_model", "unknown model")
        if tools:
            raise BrowserUIError("unsupported_tools", "native tools are not supported by this adapter")
        if self.browser.status().state != "running":
            raise BrowserUIError("browser_disconnected", "browser is not running")
        tab_id = "turn-" + uuid.uuid4().hex
        page = None
        submitted = False
        try:
            page = await self.browser.open_tab(tab_id, self.site.url)
            state = await inspect_ui(page, self.site.selectors)
            if state.state != "ready":
                raise BrowserUIError(state.state, "browser UI is not ready: " + state.state)

            s = self.site.selectors
            # Anchor to a newly created assistant message, never a previous turn.
            baseline = await self._capture_baseline(page)
            # A completed-turn marker might belong to a prior message. Record
            # whether it was absent *before* this turn was submitted.
            done_was_absent = bool(s.response_complete and not await page.locator(s.response_complete).first.is_visible())
            await page.locator(s.composer).fill(serialize_messages(messages))
            # The homepage may render an inactive/hidden Send button until
            # typing. Wait for a *visible and enabled* control before any
            # submission. Failure here is safe to retry (nothing was sent).
            send_control = await self._wait_for_send_ready(page)
            # Only submit once. An error after clicking leaves the outcome
            # ambiguous; never retry automatically.
            # Mark outcome ambiguous before clicking: click may submit remotely
            # and then raise while awaiting browser acknowledgement.
            submitted = True
            await send_control.click()
            return ProviderOutput(content=await self._wait_for_answer(page, baseline, done_was_absent))
        except BrowserUIError:
            raise
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            # Do not leak page contents, full URLs, or browser credentials.
            if not submitted:
                raise BrowserUIError("before_submit_failed", "browser interaction failed before submitting a turn") from exc
            raise BrowserUIError("remote_outcome_unknown", "browser interaction failed; submission outcome may be unknown") from exc
        finally:
            if page is not None:
                await self.browser.close_tab(tab_id)

    async def _capture_baseline(self, page: Any) -> Any:
        """Provider hook; generic websites can use conservative node counts."""
        return await page.locator(self.site.selectors.assistant_message).count()

    async def _wait_for_send_ready(self, page: Any) -> Any:
        """Wait briefly for a clickable send control, but NEVER submit here.

        No text, cookies or browser page content is logged. We inspect at most
        32 matches because responsive pages may have hidden duplicate buttons.
        """
        deadline = time.monotonic() + min(5.0, self.site.timeout_s)
        while True:
            state = await inspect_ui(page, self.site.selectors)
            if state.state in (
                "login_required", "security_challenge", "rate_limited",
                "generation_in_progress", "browser_disconnected",
            ):
                raise BrowserUIError(state.state, "browser UI is not available before submission: " + state.state)
            locator = page.locator(self.site.selectors.send_button)
            candidates = []
            if callable(getattr(locator, "count", None)) and callable(getattr(locator, "nth", None)):
                candidates = [locator.nth(i) for i in range(min(32, await locator.count()))]
            else:
                candidates = [locator.first]
            for candidate in candidates:
                if await candidate.is_visible():
                    enabled = getattr(candidate, "is_enabled", None)
                    if not callable(enabled) or await enabled():
                        return candidate
            if time.monotonic() >= deadline:
                raise BrowserUIError(
                    "submit_unavailable", "no visible, enabled Send control after typing; nothing submitted",
                )
            await asyncio.sleep(min(self.site.poll_s, 0.2))

    async def _wait_for_answer(self, page: Any, baseline: int, done_was_absent: bool) -> str:
        s = self.site.selectors
        deadline = time.monotonic() + self.site.timeout_s
        prior: str | None = None
        stable = 0
        saw_busy = False
        while time.monotonic() < deadline:
            state = await inspect_ui(page, s)
            if state.state in ("login_required", "security_challenge", "rate_limited", "browser_disconnected"):
                raise BrowserUIError(state.state, "browser UI changed during generation: " + state.state)
            if state.state == "generation_in_progress":
                saw_busy = True
            done = bool(s.response_complete and await page.locator(s.response_complete).first.is_visible())
            if s.response_complete and not done:
                done_was_absent = True
            nodes = page.locator(s.assistant_message)
            count = await nodes.count()
            if count > baseline:
                last = nodes.nth(count - 1)
                text = (await last.inner_text()).strip()
                if text and text == prior:
                    stable += 1
                else:
                    prior, stable = text, 1 if text else 0
                # Do not trust text stability alone: models may pause before
                # finishing. Require explicit complete marker or a busy-to-idle
                # transition, plus several identical observations.
                if stable >= self.site.stable_observations and (
                    (done and done_was_absent and state.state != "generation_in_progress")
                    or (saw_busy and state.state == "ready" and not s.response_complete)
                ):
                    return text
            await asyncio.sleep(self.site.poll_s)
        raise BrowserUIError("generation_timeout", "response did not visibly complete before timeout; remote outcome unknown")
