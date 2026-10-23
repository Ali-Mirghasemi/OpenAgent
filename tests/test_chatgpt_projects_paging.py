"""Bounded account-sidebar browsing; never use real ChatGPT accounts in tests."""
from pathlib import Path
from types import SimpleNamespace

import pytest

from openagent.browser import BrowserConfig, BrowserManager
from openagent.providers.chatgpt_projects import (
    scan_chatgpt_projects, wait_for_chatgpt_projects,
)
from test_ui_real_dom import LocalContentBackend, find_chromium_executable
from test_chatgpt_project_rows import OriginPage


@pytest.mark.asyncio
async def test_paged_sidebar_aggregates_virtualized_projects(tmp_path):
    executable = find_chromium_executable()
    if not executable:
        pytest.skip('Chromium executable unavailable')
    pytest.importorskip('playwright')
    html = (Path(__file__).parents[1] / 'examples' / 'chatgpt_projects_paged_fixture.html').read_text('utf-8')
    backend = LocalContentBackend(executable, html)
    browser = BrowserManager(BrowserConfig(profile_dir=tmp_path/'paged-fixture'), backend=backend)
    async with browser:
        page = OriginPage(await browser.open_tab('test', 'http://127.0.0.1:8999/projects'), 'http://127.0.0.1:8999/projects')
        report = await scan_chatgpt_projects(page, origin='http://127.0.0.1:8999',
                                             wait_seconds=1.2, max_steps=12, poll_seconds=.06)
        assert report.initial_count == 5
        assert len(report.projects) == 12
        assert len({p.id for p in report.projects}) == 12
        assert report.expand_clicks == 1
        assert report.scroll_steps > 0
        assert report.as_dict()['complete'] is False
        assert await page.evaluate('window.projectMoreClicks') == 1
        assert await page.evaluate('window.outsideMoreClicks') == 0
        assert await page.evaluate('window.unrelatedMoreClicks') == 0
        assert await page.evaluate('window.sendClicks') == 0
        await browser.close_tab('test')


@pytest.mark.asyncio
async def test_read_only_default_does_not_expand_or_scroll(tmp_path):
    executable = find_chromium_executable()
    if not executable:
        pytest.skip('Chromium executable unavailable')
    pytest.importorskip('playwright')
    html = (Path(__file__).parents[1] / 'examples' / 'chatgpt_projects_paged_fixture.html').read_text('utf-8')
    browser = BrowserManager(BrowserConfig(profile_dir=tmp_path/'default-fixture'),
                             backend=LocalContentBackend(executable, html))
    async with browser:
        page = OriginPage(await browser.open_tab('test', 'http://127.0.0.1:8999/projects'), 'http://127.0.0.1:8999/projects')
        projects = await wait_for_chatgpt_projects(page, origin='http://127.0.0.1:8999',
                                                   wait_seconds=1, poll_seconds=.05)
        assert len(projects) == 5
        assert await page.evaluate('window.projectMoreClicks') == 0
        assert await page.evaluate('window.scrolled') == 0
        await browser.close_tab('test')


@pytest.mark.asyncio
@pytest.mark.parametrize('max_steps', [0, 61])
async def test_invalid_scan_steps(max_steps):
    with pytest.raises(ValueError, match='max_steps'):
        await scan_chatgpt_projects(SimpleNamespace(url='https://chatgpt.com/'), max_steps=max_steps)

@pytest.mark.asyncio
async def test_hydration_wait_accumulates_later_project_rows(tmp_path):
    executable = find_chromium_executable()
    if not executable:
        pytest.skip('Chromium executable unavailable')
    pytest.importorskip('playwright')
    html = '''<!doctype html><html><body><aside><h2>Projects</h2><div id="rows"></div></aside>
      <script>
        let n = 0;
        const rows = document.querySelector('#rows');
        function add() {
          n++;
          const r = document.createElement('div');
          r.setAttribute('role','button'); r.setAttribute('aria-expanded','false');
          r.innerHTML = '<span data-sidebar-project-container-id="project:g-p-' +
            n.toString(16).padStart(32,'0') + '"><span data-marquee-text="true">Row ' + n + '</span></span>';
          rows.append(r);
        }
        add(); setTimeout(add, 220); setTimeout(add, 430);
      </script></body></html>'''
    browser = BrowserManager(BrowserConfig(profile_dir=tmp_path/'hydration-fixture'),
                             backend=LocalContentBackend(executable, html))
    async with browser:
        page = OriginPage(await browser.open_tab('test', 'http://127.0.0.1:8999/projects'),
                          'http://127.0.0.1:8999/projects')
        projects = await wait_for_chatgpt_projects(page, origin='http://127.0.0.1:8999',
                                                   wait_seconds=2.0, poll_seconds=.06)
        assert len(projects) == 3
        await browser.close_tab('test')
