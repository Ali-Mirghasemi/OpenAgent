"""Optional Playwright implementation. Imported only when a browser is started."""
from __future__ import annotations

from typing import Any
from .manager import BrowserConfig, BrowserPage, BrowserStartupError


class PlaywrightBackend:
    def __init__(self) -> None:
        self._playwright: Any = None
        self._context: Any = None
        self._browser: Any = None
        self._owned_context = False

    async def start(self, config: BrowserConfig) -> None:
        try:
            from playwright.async_api import async_playwright
        except ImportError as exc:
            raise BrowserStartupError('Playwright missing. Install with: pip install -e ".[browser]"') from exc
        self._playwright = await async_playwright().start()
        try:
            if config.mode == "persistent":
                browser_type = (self._playwright.firefox if config.browser == "firefox"
                                else self._playwright.chromium)
                options: dict[str, Any] = {"headless": config.headless, "timeout": config.startup_timeout_ms}
                if config.executable_path is not None:
                    browser_executable = config.executable_path.expanduser().resolve()
                    if not browser_executable.is_file():
                        raise BrowserStartupError("selected browser executable does not exist")
                    options["executable_path"] = str(browser_executable)
                elif config.browser in ("chrome", "msedge"):
                    options["channel"] = config.browser
                elif config.channel:
                    options["channel"] = config.channel
                profile_dir = config.profile_dir.expanduser().resolve()
                profile_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
                self._context = await browser_type.launch_persistent_context(str(profile_dir), **options)
                self._browser = self._context.browser
                self._owned_context = True
            else:
                self._browser = await self._playwright.chromium.connect_over_cdp(
                    config.cdp_url, timeout=config.startup_timeout_ms,
                )
                contexts = self._browser.contexts
                if not contexts:
                    raise BrowserStartupError("attached browser has no usable context")
                self._context = contexts[0]
                self._owned_context = False
        except Exception:
            await self.stop()
            raise

    def is_connected(self) -> bool:
        return self._context is not None and self._browser is not None and self._browser.is_connected()

    async def new_page(self) -> BrowserPage:
        if not self.is_connected():
            raise BrowserStartupError("browser disconnected")
        return await self._context.new_page()

    async def add_cookies(self, cookies: list[dict]) -> None:
        if not self._owned_context or self._context is None or not self.is_connected():
            raise BrowserStartupError('cookie import requires an owned persistent browser')
        await self._context.add_cookies(cookies)

    async def stop(self) -> None:
        try:
            if self._owned_context and self._context is not None:
                # Only close a browser launched by OpenAgent. Attached Chrome and
                # existing user's tabs must remain untouched.
                await self._context.close()
        finally:
            self._context = None
            self._browser = None
            self._owned_context = False
            if self._playwright is not None:
                await self._playwright.stop()  # Disposes of our CDP connection.
                self._playwright = None
                # Windows Proactor subprocess pipe finalizers can otherwise run
                # after asyncio.run() closes its loop (Python 3.10).
                import asyncio
                await asyncio.sleep(0.15)
