"""Optional Playwright DOM integration: fixture injected without external network.

A browser installation is not mandatory for package-unit CI. Runs automatically
when Chromium is present at a known system executable path.
"""
from pathlib import Path
import shutil
import pytest
from openagent.browser import BrowserConfig, BrowserManager
from openagent.browser.diagnostics import UISelectors, inspect_ui
from openagent.models import Message
from openagent.providers.browser_ui import BrowserSiteProfile, BrowserUIProvider


def find_chromium_executable():
    """Find a test browser, including OpenAgent's saved portable executable.

    Tests still use a disposable profile and locally injected HTML. The selected
    user's browser profile, cookies and remote pages are never accessed.
    """
    executable = shutil.which("chromium") or shutil.which("google-chrome")
    if executable:
        return executable
    from openagent.browser.selection import load_selection
    selected = load_selection()
    if selected.get("browser") not in {"chromium", "chrome", "msedge"}:
        return None
    path = selected.get("executable_path")
    if path and Path(path).is_file():
        return str(Path(path))
    return None


class LocalContentPage:
    def __init__(self, page, html):
        self._page = page
        self.html = html

    def __getattr__(self, name):
        return getattr(self._page, name)

    async def goto(self, url, *, wait_until, timeout):
        # No external fetch: use an actual browser engine but insert local HTML.
        await self._page.set_content(self.html, wait_until=wait_until, timeout=timeout)


class LocalContentBackend:
    def __init__(self, executable, html):
        self.executable, self.html = executable, html
        self.context = None
        self.playwright = None

    async def start(self, config):
        from playwright.async_api import async_playwright
        self.playwright = await async_playwright().start()
        self.context = await self.playwright.chromium.launch_persistent_context(
            str(config.profile_dir), executable_path=self.executable, headless=True,
        )

    def is_connected(self):
        return self.context is not None and self.context.browser.is_connected()

    async def new_page(self):
        return LocalContentPage(await self.context.new_page(), self.html)

    async def stop(self):
        if self.context:
            await self.context.close()
            self.context = None
        if self.playwright:
            await self.playwright.stop()
            self.playwright = None


@pytest.mark.asyncio
async def test_real_chromium_dom_fixture(tmp_path):
    browser_executable = find_chromium_executable()
    if browser_executable is None:
        pytest.skip("No local system Chromium executable for optional DOM integration")
    try:
        import playwright  # noqa: F401
    except ImportError:
        pytest.skip("Playwright optional dependency not installed")
    html = (Path(__file__).parents[1] / "examples" / "chat_fixture.html").read_text(encoding="utf-8")
    backend = LocalContentBackend(browser_executable, html)
    browser = BrowserManager(BrowserConfig(profile_dir=tmp_path / "local-ui"), backend=backend)
    sel = UISelectors("#oa-composer", "#oa-send", ".oa-assistant",
                      generation_in_progress="#oa-busy:not([hidden])",
                      response_complete="#oa-done:not([hidden])")
    site = BrowserSiteProfile("fixture", "fixture/ui", "http://127.0.0.1:8999/chat_fixture.html", sel)
    async with browser:
        adapter = BrowserUIProvider(site, browser)
        assert (await adapter.inspect()).state == "ready"
        result = await adapter.complete("fixture/ui", [Message(role="user", content="real DOM test")])
        assert "last user: real DOM test" in result.content
