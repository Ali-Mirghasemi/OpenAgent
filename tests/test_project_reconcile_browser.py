"""Read-only local Chromium reply verification, no live account or network."""
import pytest

from openagent.browser import BrowserConfig, BrowserManager
from openagent.providers.chatgpt_web import ChatGPTWebProvider, CHATGPT_MODEL
from openagent.providers.browser_ui import BrowserUIError
from test_project_binding import PROJECT_ID, OTHER_ID
from test_project_routing import CID
from test_chatgpt_project_rows import OriginPage
from test_ui_real_dom import LocalContentBackend, find_chromium_executable

ORIGIN = 'http://127.0.0.1:8999'
PROMPT = 'Reply with exactly: OpenAgent Project test received.'
ANSWER = 'OpenAgent Project test received.'
URL = f'{ORIGIN}/g/{PROJECT_ID}-openagent/c/{CID}'

HTML = f'''<!doctype html><html><body>
<main><article data-turn-id="user-1"><div data-message-author-role="user">{PROMPT}</div></article>
<article data-turn-id="assistant-1"><div data-message-author-role="assistant"><div class="markdown">{ANSWER}</div></div>
<button aria-label="Copy">Copy</button></article>
</main><form><div id="prompt-textarea" role="textbox" contenteditable="true"></div>
<button id="composer-submit-button" type="submit">Send</button></form>
<script>window.clicked=0;document.querySelector('form').addEventListener('submit',e=>{{e.preventDefault();window.clicked++}})</script>
</body></html>'''

class ReadOnlyBrowser:
    def __init__(self, browser, redirect=None):
        self.browser, self.config = browser, browser.config
        self.redirect = redirect
        self.tabs = {}
        self.click_counts = []
    async def start(self): await self.browser.start()
    async def open_tab(self, name, url):
        page = OriginPage(await self.browser.open_tab(name, url), self.redirect or url)
        self.tabs[name] = page
        return page
    async def close_tab(self, name):
        self.click_counts.append(await self.tabs[name].evaluate('window.clicked'))
        await self.browser.close_tab(name)

@pytest.mark.asyncio
async def test_manual_reconcile_read_only_with_slugged_route_chromium(tmp_path):
    executable = find_chromium_executable()
    if not executable: pytest.skip('No Chromium executable')
    pytest.importorskip('playwright')
    browser = BrowserManager(BrowserConfig(profile_dir=tmp_path/'browser'), backend=LocalContentBackend(executable, HTML))
    async with browser:
        proxy = ReadOnlyBrowser(browser)
        provider = ChatGPTWebProvider(proxy, url=ORIGIN+'/', timeout_s=1.6, poll_s=.04,
                    stable_observations=2, enable_conversation_continuation=True,enable_project_messages=True)
        result = await provider.reconcile_project_turn(project_id=PROJECT_ID, project_name='OpenAgent',
                        conversation_url=URL, user_message=PROMPT)
        assert result.upstream_conversation_id == CID
        assert result.content == ANSWER
        assert proxy.click_counts == [0]
        assert browser.status().open_tabs == 0

@pytest.mark.asyncio
async def test_manual_reconcile_fails_on_wrong_reply_or_other_project_chromium(tmp_path):
    executable = find_chromium_executable()
    if not executable: pytest.skip('No Chromium executable')
    pytest.importorskip('playwright')
    browser = BrowserManager(BrowserConfig(profile_dir=tmp_path/'browser'), backend=LocalContentBackend(executable, HTML))
    async with browser:
        proxy = ReadOnlyBrowser(browser)
        provider = ChatGPTWebProvider(proxy, url=ORIGIN+'/', timeout_s=1.0, poll_s=.03,
                    stable_observations=2, enable_conversation_continuation=True,enable_project_messages=True)
        with pytest.raises(BrowserUIError) as exc:
            await provider.reconcile_project_turn(project_id=PROJECT_ID, project_name='OpenAgent',
                conversation_url=URL, user_message='Other message')
        assert exc.value.code == 'reconcile_unverified'
        assert proxy.click_counts == [0]
        with pytest.raises(BrowserUIError) as exc:
            await provider.reconcile_project_turn(project_id=PROJECT_ID, project_name='OpenAgent',
                conversation_url=f'{ORIGIN}/g/{OTHER_ID}-openagent/c/{CID}', user_message=PROMPT)
        assert exc.value.code == 'reconcile_unverified'
        assert proxy.click_counts == [0]


@pytest.mark.asyncio
async def test_reconcile_rejects_reply_without_copy_completion_chromium(tmp_path):
    executable = find_chromium_executable()
    if not executable: pytest.skip('No Chromium executable')
    pytest.importorskip('playwright')
    html = HTML.replace('aria-label="Copy"', 'aria-label="Share"')
    browser = BrowserManager(BrowserConfig(profile_dir=tmp_path/'browser'), backend=LocalContentBackend(executable, html))
    async with browser:
        proxy = ReadOnlyBrowser(browser)
        provider = ChatGPTWebProvider(proxy, url=ORIGIN+'/', timeout_s=1, poll_s=.03,
                    stable_observations=2, enable_conversation_continuation=True, enable_project_messages=True)
        with pytest.raises(BrowserUIError) as exc:
            await provider.reconcile_project_turn(project_id=PROJECT_ID, project_name='OpenAgent',
                conversation_url=URL, user_message=PROMPT)
        assert exc.value.code == 'reconcile_unverified'
        assert proxy.click_counts == [0]
