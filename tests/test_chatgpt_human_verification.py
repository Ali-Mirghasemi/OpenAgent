"""Passive challenge classification; no CAPTCHA solving, clicks, or live sites."""
import asyncio

import pytest

from openagent.browser import BrowserConfig, BrowserManager
from openagent.browser.challenge import inspect_human_verification
from openagent.browser.diagnostics import inspect_ui
from openagent.browser.surface import observe_status
from openagent.providers.chatgpt_web import CHATGPT_SELECTORS, ChatGPTWebProvider
from openagent.providers.browser_ui import BrowserUIError
from test_ui_real_dom import LocalContentBackend, find_chromium_executable


async def fixture_browser(tmp_path, html):
    executable = find_chromium_executable()
    if not executable:
        pytest.skip('No Chromium executable')
    pytest.importorskip('playwright')
    return BrowserManager(BrowserConfig(profile_dir=tmp_path),
                          backend=LocalContentBackend(executable, html))


@pytest.mark.asyncio
@pytest.mark.parametrize('html,expected', [
    ('<title>Just a moment...</title><body><div>Cloudflare</div></body>', 'challenge_title'),
    ('<title>ChatGPT</title><body><div id="challenge-form"><span>Verify you are human</span></div></body>', 'challenge_markup'),
    ('<title>ChatGPT</title><body><p>Verify you are human</p></body>', 'human_verification_prompt'),
])
async def test_challenge_markers_are_detected_without_iframe(tmp_path, html, expected):
    browser = await fixture_browser(tmp_path, '<!doctype html><html>'+html+'</html>')
    async with browser:
        page = await browser.open_tab('check', 'http://127.0.0.1:8999/')
        assert await inspect_human_verification(page) == expected
        observation = await inspect_ui(page, CHATGPT_SELECTORS)
        assert observation.state == 'security_challenge'
        assert observation.evidence == expected
        await browser.close_tab('check')


@pytest.mark.asyncio
async def test_actual_chat_text_does_not_trigger_challenge(tmp_path):
    html = '''<!doctype html><html><head><title>ChatGPT</title></head><body><main>
       <div data-message-author-role="user">Verify you are human</div>
       <div id="prompt-textarea" contenteditable="true" role="textbox"></div>
       <button data-testid="send-button">Send</button></main></body></html>'''
    browser = await fixture_browser(tmp_path, html)
    async with browser:
        page = await browser.open_tab('check', 'http://127.0.0.1:8999/')
        assert await inspect_human_verification(page) is None
        assert (await inspect_ui(page, CHATGPT_SELECTORS)).state == 'ready'
        await browser.close_tab('check')


@pytest.mark.asyncio
async def test_feature_inventory_reports_challenge_not_ui_changed(tmp_path):
    html = '''<!doctype html><html><head><title>Just a moment...</title></head><body>
      <div>Verify you are human</div></body></html>'''
    browser = await fixture_browser(tmp_path, html)
    async with browser:
        provider = ChatGPTWebProvider(browser, url='http://127.0.0.1:8999/', timeout_s=2)
        info = await provider.inspect_feature_controls(wait_seconds=1)
        assert info['page_state'] == 'security_challenge', info
        assert info['ui_evidence'] == 'challenge_title', info
        assert info['waited_seconds'] < 1
        assert info['surface']['main_present'] is False
        assert set(info['controls'].values()) == {'unavailable'}
        assert info['selection_changed'] is False and info['uploaded'] is False
        assert info['submitted'] is False


@pytest.mark.asyncio
async def test_delayed_challenge_stops_waiting_when_detected(tmp_path):
    html = '''<!doctype html><html><head><title>ChatGPT</title></head><body>
      <script>setTimeout(()=>{document.title='Just a moment...';
      document.body.insertAdjacentHTML('beforeend','<div id="challenge-form">Verify you are human</div>')},150)</script>
      </body></html>'''
    browser = await fixture_browser(tmp_path, html)
    async with browser:
        page = await browser.open_tab('check', 'http://127.0.0.1:8999/')
        obs, _, elapsed = await observe_status(page, CHATGPT_SELECTORS,
                                               wait_seconds=1.5, poll_seconds=.05)
        assert obs.state == 'security_challenge'
        assert .1 <= elapsed < 1.5
        await browser.close_tab('check')


@pytest.mark.asyncio
async def test_page_error_preflight_reports_challenge(tmp_path):
    html = '''<!doctype html><html><head><title>Just a moment...</title></head>
      <body><p>Verify you are human</p></body></html>'''
    browser = await fixture_browser(tmp_path, html)
    async with browser:
        page = await browser.open_tab('check', 'http://127.0.0.1:8999/')
        with pytest.raises(BrowserUIError) as error:
            await ChatGPTWebProvider._check_page_error(page)
        assert error.value.code == 'security_challenge'
        await browser.close_tab('check')
