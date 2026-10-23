"""UI fixture for a generic Show more next to Project rows, not a semantic section."""
from pathlib import Path

import pytest
from openagent.browser import BrowserConfig, BrowserManager
from openagent.providers.chatgpt_projects import scan_chatgpt_projects, wait_for_chatgpt_projects
from test_chatgpt_project_rows import OriginPage
from test_ui_real_dom import LocalContentBackend, find_chromium_executable


@pytest.mark.asyncio
async def test_scoped_generic_show_more_expands_projects_without_unrelated_clicks(tmp_path):
    executable = find_chromium_executable()
    if not executable:
        pytest.skip('Chromium executable unavailable')
    pytest.importorskip('playwright')
    html = (Path(__file__).parents[1] / 'examples' / 'chatgpt_projects_generic_more_fixture.html').read_text('utf-8')
    browser = BrowserManager(BrowserConfig(profile_dir=tmp_path/'generic-more'),
                             backend=LocalContentBackend(executable, html))
    async with browser:
        page = OriginPage(await browser.open_tab('test', 'http://127.0.0.1:8999/projects'),
                          'http://127.0.0.1:8999/projects')
        report = await scan_chatgpt_projects(page, origin='http://127.0.0.1:8999',
                                             wait_seconds=1, max_steps=12, poll_seconds=.15)
        assert report.initial_count == 5
        assert report.expand_clicks == 1
        assert report.as_dict()['more_controls_seen'] >= 3
        assert len(report.projects) == 11
        assert await page.evaluate('window.projectMoreClicks') == 1
        assert await page.evaluate('window.unrelatedMoreClicks') == 0
        assert await page.evaluate('window.outsideMoreClicks') == 0
        await browser.close_tab('test')


@pytest.mark.asyncio
async def test_passive_inventory_does_not_click_generic_show_more(tmp_path):
    executable = find_chromium_executable()
    if not executable:
        pytest.skip('Chromium executable unavailable')
    pytest.importorskip('playwright')
    html = (Path(__file__).parents[1] / 'examples' / 'chatgpt_projects_generic_more_fixture.html').read_text('utf-8')
    browser = BrowserManager(BrowserConfig(profile_dir=tmp_path/'generic-passive'),
                             backend=LocalContentBackend(executable, html))
    async with browser:
        page = OriginPage(await browser.open_tab('test', 'http://127.0.0.1:8999/projects'),
                          'http://127.0.0.1:8999/projects')
        projects = await wait_for_chatgpt_projects(page, origin='http://127.0.0.1:8999',
                                                   wait_seconds=1, poll_seconds=.1)
        assert len(projects) == 5
        assert await page.evaluate('window.projectMoreClicks') == 0
        await browser.close_tab('test')


@pytest.mark.asyncio
async def test_ambiguous_generic_show_more_does_not_guess(tmp_path):
    executable = find_chromium_executable()
    if not executable:
        pytest.skip('Chromium executable unavailable')
    pytest.importorskip('playwright')
    original = (Path(__file__).parents[1] / 'examples' / 'chatgpt_projects_generic_more_fixture.html').read_text('utf-8')
    html = original.replace('</div>\n  <section aria-label="Recents">',
        '<button id="ambiguous-more" type="button">Show more</button></div>\n  <section aria-label="Recents">')
    browser = BrowserManager(BrowserConfig(profile_dir=tmp_path/'ambiguous-more'),
                             backend=LocalContentBackend(executable, html))
    async with browser:
        page = OriginPage(await browser.open_tab('test', 'http://127.0.0.1:8999/projects'),
                          'http://127.0.0.1:8999/projects')
        report = await scan_chatgpt_projects(page, origin='http://127.0.0.1:8999',
                                             wait_seconds=1, max_steps=3, poll_seconds=.08)
        assert report.expand_clicks == 0
        assert len(report.projects) == 5
        assert await page.evaluate('window.projectMoreClicks') == 0
        assert await page.evaluate('window.unrelatedMoreClicks') == 0
        await browser.close_tab('test')
