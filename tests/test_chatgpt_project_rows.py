"""Synthetic 2026 ChatGPT Project sidebar rows, never a live account."""
from pathlib import Path
from types import SimpleNamespace

import pytest

from openagent.browser import BrowserConfig, BrowserManager
from openagent.providers.chatgpt_projects import (
    discover_chatgpt_projects, wait_for_chatgpt_projects,
)
from test_ui_real_dom import LocalContentBackend, find_chromium_executable


class OriginPage:
    def __init__(self, wrapped, url):
        self._wrapped = wrapped
        self.url = url

    def __getattr__(self, key):
        return getattr(self._wrapped, key)


PROJECT_A = 'g-p-aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa-dev'
PROJECT_B = 'g-p-bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb-demo'


@pytest.mark.asyncio
async def test_real_chromium_project_rows_read_only(tmp_path):
    executable = find_chromium_executable()
    if not executable:
        pytest.skip('Chromium executable unavailable')
    pytest.importorskip('playwright')
    html = (Path(__file__).parents[1] / 'examples' / 'chatgpt_project_rows_fixture.html').read_text('utf-8')
    browser = BrowserManager(BrowserConfig(profile_dir=tmp_path/'project-fixture'),
                             backend=LocalContentBackend(executable, html))
    async with browser:
        page = OriginPage(await browser.open_tab('test', 'http://127.0.0.1:8999/project-fixture.html'), 'http://127.0.0.1:8999/project-fixture.html')
        projects = await discover_chatgpt_projects(page, origin='http://127.0.0.1:8999')
        assert [(p.id, p.name, p.source) for p in projects] == [
            (PROJECT_A, 'Software Lab', 'sidebar-row'),
            (PROJECT_B, 'Research Project', 'sidebar-row'),
        ]
        assert all(p.url.startswith('http://127.0.0.1:8999/g/') for p in projects)
        assert all('Private conversation' not in p.name for p in projects)
        assert await page.evaluate('window.clicked') == 0
        assert await discover_chatgpt_projects(page) == []  # cross-origin fail-closed
        await browser.close_tab('test')


@pytest.mark.asyncio
async def test_real_chromium_delayed_sidebar_hydration(tmp_path):
    executable = find_chromium_executable()
    if not executable:
        pytest.skip('Chromium executable unavailable')
    pytest.importorskip('playwright')
    html = '''<!doctype html><html><body><nav id="sidebar"></nav>
      <script>setTimeout(() => {
        document.querySelector('#sidebar').innerHTML = `
        <div role="button" aria-expanded="false">
          <span data-sidebar-project-container-id="project:g-p-aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa-dev">
           <span data-marquee-text="true">Delayed Project</span>
          </span>
        </div>`;
      }, 200);</script></body></html>'''
    browser = BrowserManager(BrowserConfig(profile_dir=tmp_path/'delayed-fixture'),
                             backend=LocalContentBackend(executable, html))
    async with browser:
        page = OriginPage(await browser.open_tab('test', 'http://127.0.0.1:8999/delayed-project.html'), 'http://127.0.0.1:8999/delayed-project.html')
        projects = await wait_for_chatgpt_projects(page, origin='http://127.0.0.1:8999',
                                                   wait_seconds=2, poll_seconds=.05)
        assert len(projects) == 1
        assert projects[0].name == 'Delayed Project'
        await browser.close_tab('test')


@pytest.mark.asyncio
@pytest.mark.parametrize('seconds', [-1, 31])
async def test_project_wait_invalid_duration(seconds):
    with pytest.raises(ValueError, match='wait_seconds'):
        await wait_for_chatgpt_projects(SimpleNamespace(url='https://chatgpt.com/'),
                                        wait_seconds=seconds)
