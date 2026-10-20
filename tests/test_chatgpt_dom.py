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
