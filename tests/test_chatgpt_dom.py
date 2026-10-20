"""Actual Chromium DOM contract (locally injected HTML, no remote calls)."""
from pathlib import Path

import pytest

from openagent.browser import BrowserConfig, BrowserManager
from openagent.models import Message
from openagent.providers.chatgpt_web import CHATGPT_MODEL, ChatGPTWebProvider
from test_ui_real_dom import LocalContentBackend, find_chromium_executable


@pytest.mark.asyncio
async def test_chatgpt_scoped_completion_real_chromium(tmp_path):
    executable = find_chromium_executable()
    if not executable:
        pytest.skip('No installed Chromium for optional real DOM test')
    try:
        import playwright  # noqa: F401
    except ImportError:
        pytest.skip('Playwright not installed')

    html = (Path(__file__).parents[1] / 'examples' / 'chatgpt_fixture.html').read_text(encoding='utf-8')
    backend = LocalContentBackend(executable, html)
    browser = BrowserManager(BrowserConfig(profile_dir=tmp_path/'profile'), backend=backend)
    provider = ChatGPTWebProvider(browser, timeout_s=3, poll_s=.04, stable_observations=3,
                                   url='http://127.0.0.1:8999/chatgpt_fixture.html')
    async with browser:
        obs = await provider.inspect()
        assert obs.state == 'ready'
        output = await provider.complete(CHATGPT_MODEL, [Message(role='user',content='hello')])
        assert output.content == 'Final answer from browser DOM'
        assert browser.status().open_tabs == 0


@pytest.mark.asyncio
async def test_anonymous_chatgpt_layout_real_chromium(tmp_path):
    """Regression for the screenshot: visible Log in, anonymous composer.

    The first matching button is hidden; the second is visible. No login,
    network call to ChatGPT, cookie handling or message submission occurs.
    """
    executable = find_chromium_executable()
    if not executable:
        pytest.skip('No installed Chromium for optional real DOM test')
    try:
        import playwright  # noqa: F401
    except ImportError:
        pytest.skip('Playwright not installed')
    from openagent.browser.diagnostics import inspect_ui_controls
    from openagent.providers.browser_ui import BrowserUIError

    html = (Path(__file__).parents[1] / 'examples' / 'chatgpt_logged_out_fixture.html').read_text(encoding='utf-8')
    backend = LocalContentBackend(executable, html)
    browser = BrowserManager(BrowserConfig(profile_dir=tmp_path/'anon-profile'), backend=backend)
    provider = ChatGPTWebProvider(browser, url='http://127.0.0.1:8999/anon.html')
    async with browser:
        obs = await provider.inspect()
        assert obs.state == 'login_required'
        assert obs.evidence == 'login_required'
        page = await browser.open_tab('controls', provider.site.url)
        controls = await inspect_ui_controls(page, provider.site.selectors)
        assert controls['login_required'] == 'visible'
        assert controls['composer'] == 'visible'
        assert controls['send_button'] == 'visible'
        await browser.close_tab('controls')
        with pytest.raises(BrowserUIError) as error:
            await provider.complete(CHATGPT_MODEL, [Message(role='user', content='should not submit')])
        assert error.value.code == 'login_required'
