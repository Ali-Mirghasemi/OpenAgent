"""Bounded SPA-hydration control scan; no live account or prompt submission."""
import pytest

from openagent.browser import BrowserConfig, BrowserManager
from openagent.providers.chatgpt_web import ChatGPTWebProvider
from test_ui_real_dom import LocalContentBackend, find_chromium_executable


@pytest.mark.asyncio
async def test_feature_scan_waits_for_hydrated_composer_and_optional_controls(tmp_path):
    executable = find_chromium_executable()
    if not executable:
        pytest.skip("No local Chromium")
    pytest.importorskip("playwright")
    html = '''<!doctype html><html><body><main id="app"></main>
      <script>
      window.uiActions=0;
      setTimeout(()=>document.querySelector('#app').innerHTML =
        '<div id="prompt-textarea" contenteditable="true" role="textbox"></div>', 210);
      setTimeout(()=>document.querySelector('#app').insertAdjacentHTML('beforeend',
        '<button data-testid="model-switcher-dropdown-button">Model</button>'), 620);
      setTimeout(()=>document.querySelector('#app').insertAdjacentHTML('beforeend',
        '<input type="file" hidden>'), 820);
      </script></body></html>'''
    browser = BrowserManager(BrowserConfig(profile_dir=tmp_path/'hydrating'),
        backend=LocalContentBackend(executable, html))
    async with browser:
        provider = ChatGPTWebProvider(browser, url='http://127.0.0.1:8999/', timeout_s=3)
        data = await provider.inspect_feature_controls(wait_seconds=2.5)
        assert data['page_state'] == 'ready', data
        assert data['ui_evidence'] == 'composer_visible_send_inactive'
        assert data['controls']['model_picker'] == 'detected', data
        assert data['controls']['file_input'] == 'detected', data
        assert data['waited_seconds'] >= 0.8
        assert data['surface']['ready_state'] == 'complete'
        assert data['submitted'] is False and data['selection_changed'] is False
        assert data['uploaded'] is False
        assert browser.status().open_tabs == 0


@pytest.mark.asyncio
async def test_feature_scan_waits_out_missing_composer_and_does_not_claim_ready(tmp_path):
    executable = find_chromium_executable()
    if not executable:
        pytest.skip('No local Chromium')
    pytest.importorskip('playwright')
    html = '<!doctype html><html><body><main><div>Not ready</div></main></body></html>'
    browser = BrowserManager(BrowserConfig(profile_dir=tmp_path/'missing'),
        backend=LocalContentBackend(executable, html))
    async with browser:
        provider = ChatGPTWebProvider(browser, url='http://127.0.0.1:8999/', timeout_s=3)
        data = await provider.inspect_feature_controls(wait_seconds=.55)
        assert data['page_state'] == 'ui_changed', data
        assert data['ui_evidence'] == 'expected_controls_missing'
        assert data['waited_seconds'] >= .5
        assert data['surface']['main_present'] is True
        assert data['controls']['model_picker'] == 'not_detected'
        assert data['submitted'] is False


@pytest.mark.asyncio
async def test_feature_scan_wait_seconds_validation(tmp_path):
    browser = BrowserManager(BrowserConfig(profile_dir=tmp_path/'unused'))
    provider = ChatGPTWebProvider(browser)
    for invalid in (-1, 30.01):
        with pytest.raises(ValueError, match='0-30'):
            await provider.inspect_feature_controls(wait_seconds=invalid)
