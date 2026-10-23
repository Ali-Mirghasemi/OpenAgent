"""Real Chromium, local injected HTML only: no network or signed-in profile."""
import pytest

from openagent.browser import BrowserConfig, BrowserManager
from openagent.models import Message
from openagent.providers.browser_ui import BrowserUIError
from openagent.providers.chatgpt_web import ChatGPTWebProvider, CHATGPT_MODEL
from test_chatgpt_project_rows import OriginPage
from test_project_binding import PROJECT_ID, OTHER_ID
from test_project_routing import CID
from test_ui_real_dom import LocalContentBackend, find_chromium_executable

ORIGIN = 'http://127.0.0.1:8999'


class RouteBrowser:
    def __init__(self, browser, *, wrong_redirect=False):
        self.browser, self.config = browser, browser.config
        self.wrong_redirect = wrong_redirect
        self.pages, self.closed, self.drafts = [], [], []

    async def start(self): await self.browser.start()
    async def open_tab(self, name, url):
        if self.wrong_redirect:
            url = url.replace(PROJECT_ID, OTHER_ID)
        page = OriginPage(await self.browser.open_tab(name, url), url)
        self.pages.append(page)
        return page
    async def close_tab(self, name):
        self.closed.append(await self.pages[-1].evaluate('window.sent'))
        self.drafts.append(await self.pages[-1].evaluate('window.lastDraft'))
        await self.browser.close_tab(name)


HTML = '''<!doctype html><html><body>
<main><h1>OpenAgent</h1><article data-turn-id="old"><div data-message-author-role="assistant">
<div class="markdown">Earlier message</div></div><button data-testid="copy-turn-action-button">Copy</button></article></main>
<form id="chat"><div contenteditable="true" role="textbox" id="prompt-textarea"></div>
<button id="composer-submit-button" type="submit" hidden disabled aria-disabled="true">Send</button></form>
<script>window.sent=0; window.lastDraft='';
const box=document.querySelector('#prompt-textarea'),btn=document.querySelector('#composer-submit-button');
box.addEventListener('input',()=>{btn.hidden=!box.textContent.trim();btn.disabled=btn.hidden;
btn.setAttribute('aria-disabled', btn.disabled ? 'true' : 'false')});
document.querySelector('#chat').addEventListener('submit',e=>{e.preventDefault();
if(e.submitter===btn && !btn.disabled){window.sent++;window.lastDraft=box.textContent;}})
</script></body></html>'''


@pytest.mark.asyncio
async def test_verified_project_first_and_second_turn_chromium(tmp_path):
    binary = find_chromium_executable()
    if binary is None:
        pytest.skip('No Chromium executable')
    pytest.importorskip('playwright')
    browser = BrowserManager(BrowserConfig(profile_dir=tmp_path/'temp'), backend=LocalContentBackend(binary, HTML))
    async with browser:
        route = RouteBrowser(browser)
        provider = ChatGPTWebProvider(route, url=ORIGIN+'/', timeout_s=1, poll_s=.02,
                                      enable_conversation_continuation=True, enable_project_messages=True)
        async def checked_answer(page, baseline, done_was_absent):
            assert await page.evaluate('window.sent') == 1
            page.url = f'{ORIGIN}/g/{PROJECT_ID}/c/{CID}'
            return 'Project answer'
        provider._wait_for_answer = checked_answer
        first = await provider.complete_in_project_session(
            CHATGPT_MODEL, [Message(role='user', content='Hello Project')],
            project_id=PROJECT_ID, project_name='OpenAgent', upstream_conversation_id=None)
        assert first.upstream_conversation_id == CID
        assert route.pages[0].url == f'{ORIGIN}/g/{PROJECT_ID}/c/{CID}'
        assert route.closed == [1]
        second = await provider.complete_in_project_session(
            CHATGPT_MODEL, [Message(role='user', content='Hello Project'),
                            Message(role='assistant', content='Project answer'),
                            Message(role='user', content='Follow up')],
            project_id=PROJECT_ID, project_name='OpenAgent', upstream_conversation_id=CID)
        assert second.content == 'Project answer'
        assert route.closed == [1, 1]
        assert route.drafts == ['Hello Project', 'Follow up']
        assert browser.status().open_tabs == 0


@pytest.mark.asyncio
async def test_wrong_project_redirect_never_clicks_send(tmp_path):
    binary = find_chromium_executable()
    if binary is None:
        pytest.skip('No Chromium executable')
    pytest.importorskip('playwright')
    browser = BrowserManager(BrowserConfig(profile_dir=tmp_path/'temp'), backend=LocalContentBackend(binary, HTML))
    async with browser:
        route = RouteBrowser(browser, wrong_redirect=True)
        provider = ChatGPTWebProvider(route, url=ORIGIN+'/', timeout_s=1, poll_s=.02,
                                      enable_conversation_continuation=True, enable_project_messages=True)
        with pytest.raises(BrowserUIError) as exc:
            await provider.complete_in_project_session(
                CHATGPT_MODEL, [Message(role='user', content='Never submit')],
                project_id=PROJECT_ID, project_name='OpenAgent', upstream_conversation_id=None)
        assert exc.value.code == 'project_unverified'
        assert route.closed == [0]
        assert browser.status().open_tabs == 0
