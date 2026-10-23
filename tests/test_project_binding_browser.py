"""Opt-in Project verification with local Chromium; no network or account access."""
import pytest
from openagent.browser import BrowserConfig, BrowserManager
from openagent.providers.chatgpt_web import ChatGPTWebProvider
from test_chatgpt_project_rows import OriginPage
from test_ui_real_dom import LocalContentBackend, find_chromium_executable
from test_project_binding import PROJECT_ID

ORIGIN = "http://127.0.0.1:8999"


class RouteBrowser:
    """Give local HTML a synthetic validated URL after set_content()."""
    def __init__(self, manager):
        self.manager = manager
        self.pages = []
        self.config = manager.config
        self.sent_counts = []

    async def start(self):
        return await self.manager.start()

    async def open_tab(self, key, url):
        page = OriginPage(await self.manager.open_tab(key, url), url)
        self.pages.append(page)
        return page

    async def close_tab(self, key):
        page = self.pages[-1]
        self.sent_counts.append(await page.evaluate("window.submitted"))
        await self.manager.close_tab(key)


@pytest.mark.asyncio
@pytest.mark.parametrize("heading,verified", [("OpenAgent", True), ("Other", False)])
async def test_provider_verified_project_with_local_chromium(tmp_path, heading, verified):
    executable = find_chromium_executable()
    if executable is None:
        pytest.skip("Chromium test executable unavailable")
    pytest.importorskip("playwright")
    html = f'''<!DOCTYPE html><html><body>
      <nav><div role="button" aria-expanded="false">
        <span data-sidebar-project-container-id="project:{PROJECT_ID}">
        <span data-marquee-text="true">OpenAgent</span></span></div></nav>
      <main><h1>{heading}</h1></main>
      <script>window.submitted = 0</script>
      </body></html>'''
    mgr = BrowserManager(BrowserConfig(profile_dir=tmp_path / heading),
                         backend=LocalContentBackend(executable, html))
    async with mgr:
        route = RouteBrowser(mgr)
        provider = ChatGPTWebProvider(route, url=f"{ORIGIN}/", timeout_s=2.0)
        result = await provider.verify_project(PROJECT_ID, wait_seconds=.3, max_steps=2)
        assert result is not None
        assert (result.state == "verified") is verified
        assert result.project_id == PROJECT_ID
        assert len(route.pages) == 2
        assert route.sent_counts == [0, 0]
        assert mgr.status().open_tabs == 0
