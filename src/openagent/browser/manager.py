"""Browser context lifecycle shared by authorized provider adapters.

The browser manager does not log in, extract cookies, bypass challenges, submit
prompts, or retry requests. It owns only pages it creates; in CDP mode it does
not close the attached user's browser or pre-existing tabs.
"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal, Protocol
from urllib.parse import urlsplit


class BrowserError(RuntimeError):
    """Expected, sanitized browser lifecycle failure."""


class BrowserStartupError(BrowserError):
    pass


class BrowserDisconnected(BrowserError):
    pass


class BrowserSessionError(BrowserError):
    pass


class BrowserNavigationError(BrowserError):
    pass


@dataclass(frozen=True)
class BrowserConfig:
    mode: Literal["persistent", "cdp"] = "persistent"
    profile_dir: Path = field(default_factory=lambda: Path.home() / ".openagent" / "browser-profile")
    cdp_url: str = "http://127.0.0.1:9222"
    headless: bool = True
    channel: str | None = None
    browser: Literal["chromium", "chrome", "msedge", "firefox"] = "chromium"
    executable_path: Path | None = None
    startup_timeout_ms: int = 30_000
    navigation_timeout_ms: int = 30_000
    max_tabs: int = 4
    allowed_origins: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if self.browser not in ("chromium", "chrome", "msedge", "firefox"):
            raise ValueError("invalid browser type")
        if self.mode == "cdp" and self.browser == "firefox":
            raise ValueError("Firefox cannot attach via Chromium CDP; use persistent mode")
        if self.executable_path is not None and self.channel:
            raise ValueError("choose executable_path or channel, not both")
        if self.browser == "firefox" and self.channel:
            raise ValueError("channel is not supported for Firefox")
        if self.mode not in ("persistent", "cdp"):
            raise ValueError("invalid browser mode")
        if not (100 <= self.startup_timeout_ms <= 300_000):
            raise ValueError("startup_timeout_ms out of range")
        if not (100 <= self.navigation_timeout_ms <= 300_000):
            raise ValueError("navigation_timeout_ms out of range")
        if not (1 <= self.max_tabs <= 64):
            raise ValueError("max_tabs must be from 1 to 64")
        if self.mode == "cdp":
            parsed = urlsplit(self.cdp_url)
            if parsed.scheme not in ("http", "ws") or parsed.hostname not in ("127.0.0.1", "localhost", "::1"):
                raise ValueError("CDP endpoint must be loopback http/ws")
            if parsed.username or parsed.password or parsed.query or parsed.fragment:
                raise ValueError("CDP URL cannot contain credentials, query or fragment")
        else:
            # Chrome/Edge default User Data must never be reused by the automation.
            p = self.profile_dir.expanduser().resolve()
            parts = [part.lower() for part in p.parts]
            if (
                p.name.lower() in ("default", "user data")
                or (len(parts) >= 2 and parts[-2:] in (["google", "chrome"], ["microsoft", "edge"]))
            ):
                raise ValueError("use a dedicated OpenAgent browser profile, not Chrome's default profile")
        for origin in self.allowed_origins:
            if origin != _origin(origin):
                raise ValueError("allowed_origins entries must be full origins without paths")


def _origin(url: str) -> str:
    """Validate an adapter-supplied navigation URL and extract its origin."""
    try:
        parsed = urlsplit(url)
        hostname = parsed.hostname
        port = parsed.port  # Reject invalid ports rather than deferring to the browser.
    except ValueError as exc:
        raise BrowserNavigationError("invalid navigation URL") from exc
    if not hostname or parsed.username or parsed.password:
        raise BrowserNavigationError("navigation URL must not contain credentials")
    if parsed.scheme != "https" and not (parsed.scheme == "http" and hostname in ("localhost", "127.0.0.1", "::1")):
        raise BrowserNavigationError("only HTTPS or loopback HTTP navigation is allowed")
    # Enforce canonical, lowercase origin string. IPv6 requires square brackets.
    host = f"[{hostname}]" if ":" in hostname else hostname
    return f"{parsed.scheme}://{host}{':' + str(port) if port is not None else ''}"


class BrowserPage(Protocol):
    async def goto(self, url: str, *, wait_until: str, timeout: int) -> Any: ...
    async def close(self) -> None: ...
    def is_closed(self) -> bool: ...


class BrowserBackend(Protocol):
    async def start(self, config: BrowserConfig) -> None: ...
    async def new_page(self) -> BrowserPage: ...
    async def stop(self) -> None: ...
    async def add_cookies(self, cookies: list[dict]) -> None: ...
    def is_connected(self) -> bool: ...


@dataclass(frozen=True)
class BrowserStatus:
    state: Literal["stopped", "starting", "running", "failed", "disconnected"]
    mode: str
    open_tabs: int
    reason: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "state": self.state,
            "mode": self.mode,
            "open_tabs": self.open_tabs,
            "reason": self.reason,
        }


class BrowserManager:
    """One browser transport, explicit per-request tabs, no implicit navigation retries."""

    def __init__(self, config: BrowserConfig, *, backend: BrowserBackend | None = None) -> None:
        self.config = config
        if backend is None:
            from .playwright_backend import PlaywrightBackend
            backend = PlaywrightBackend()
        self._backend = backend
        self._tabs: dict[str, BrowserPage] = {}
        self._state: Literal["stopped", "starting", "running", "failed", "disconnected"] = "stopped"
        self._reason: str | None = None
        self._lock = asyncio.Lock()

    def status(self) -> BrowserStatus:
        if self._state == "running" and not self._backend.is_connected():
            self._state = "disconnected"
            self._reason = "browser_connection_lost"
        count = sum(not page.is_closed() for page in self._tabs.values())
        return BrowserStatus(self._state, self.config.mode, count, self._reason)

    async def start(self) -> BrowserStatus:
        async with self._lock:
            if self.status().state == "running":
                return self.status()
            # After a disconnect, do not recover unfinished requests; their
            # outcome must be reconciled by the caller/session journal.
            if self._state == "disconnected":
                self._tabs.clear()
                await self._backend.stop()
            self._state, self._reason = "starting", None
            try:
                await self._backend.start(self.config)
                if not self._backend.is_connected():
                    raise BrowserStartupError("browser did not become connected")
            except Exception as exc:
                self._state, self._reason = "failed", "startup_failed"
                try:
                    await self._backend.stop()
                except Exception:
                    pass
                # Backend-raised BrowserStartupError messages are intentionally
                # sanitized. Other driver exceptions may contain private paths.
                message = (str(exc) if isinstance(exc, BrowserStartupError)
                           else "browser startup failed; verify browser compatibility/path or the local CDP endpoint")
                raise BrowserStartupError(message) from exc
            self._state = "running"
            return self.status()

    async def open_tab(self, tab_id: str, url: str) -> BrowserPage:
        """Open exactly one new tab. Retrying a failed submission is NEVER implied."""
        if not tab_id or len(tab_id) > 128:
            raise BrowserSessionError("tab_id must be 1 to 128 characters")
        origin = _origin(url)
        if self.config.allowed_origins and origin not in self.config.allowed_origins:
            raise BrowserNavigationError("origin not allowed by this provider")
        async with self._lock:
            if self.status().state != "running":
                raise BrowserDisconnected("browser not connected")
            if tab_id in self._tabs:
                raise BrowserSessionError("tab_id already allocated")
            if self.status().open_tabs >= self.config.max_tabs:
                raise BrowserSessionError("browser tab limit reached")
            page: BrowserPage | None = None
            try:
                page = await self._backend.new_page()
                await page.goto(url, wait_until="domcontentloaded", timeout=self.config.navigation_timeout_ms)
                if page.is_closed():
                    raise BrowserNavigationError("page closed during navigation")
            except Exception as exc:
                if page is not None:
                    try:
                        await page.close()
                    except Exception:
                        pass
                raise BrowserNavigationError("browser navigation failed") from exc
            self._tabs[tab_id] = page
            return page

    async def install_site_cookies(self, site: str, cookies: list[dict]) -> int:
        """Write explicit site-scoped cookies only to our OWN persistent context.

        Caller must obtain user confirmation before invoking. Does not inspect
        existing browser cookies or report any secret values.
        """
        if self.config.mode != 'persistent':
            raise BrowserSessionError('cookie import requires an owned persistent profile')
        if self.status().state != 'running':
            raise BrowserDisconnected('browser not connected')
        if not cookies:
            return 0
        for cookie in cookies:
            if cookie.get('domain') not in (site, '.' + site):
                raise BrowserSessionError('cookie outside approved site scope')
        try:
            await self._backend.add_cookies(cookies)
        except Exception as exc:
            # A Playwright protocol error can contain cookie metadata, or even
            # secret data from the supplied payload. Do not expose the original
            # exception through the normal CLI. Preserve it only as a cause.
            raise BrowserSessionError('browser rejected site-cookie import') from exc
        return len(cookies)

    def tab(self, tab_id: str) -> BrowserPage:
        if self.status().state != "running":
            raise BrowserDisconnected("browser not connected")
        page = self._tabs.get(tab_id)
        if page is None or page.is_closed():
            raise BrowserSessionError("unknown or closed tab")
        return page

    async def close_tab(self, tab_id: str) -> None:
        async with self._lock:
            page = self._tabs.pop(tab_id, None)
            if page is not None and not page.is_closed():
                try:
                    await page.close()
                except Exception:
                    self._state, self._reason = "disconnected", "tab_close_failed"
                    raise BrowserDisconnected("could not close managed tab") from None

    async def stop(self) -> None:
        async with self._lock:
            for page in self._tabs.values():
                if not page.is_closed():
                    try:
                        await page.close()
                    except Exception:
                        pass
            self._tabs.clear()
            try:
                await self._backend.stop()
            finally:
                self._state, self._reason = "stopped", None

    async def __aenter__(self) -> "BrowserManager":
        await self.start()
        return self

    async def __aexit__(self, *_: Any) -> None:
        await self.stop()
