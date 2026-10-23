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
        assert exc.value.code == 'reconcile_user_text_mismatch'
        assert proxy.click_counts == [0]
        with pytest.raises(BrowserUIError) as exc:
            await provider.reconcile_project_turn(project_id=PROJECT_ID, project_name='OpenAgent',
                conversation_url=f'{ORIGIN}/g/{OTHER_ID}-openagent/c/{CID}', user_message=PROMPT)
        assert exc.value.code == 'reconcile_invalid_input'
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
        assert exc.value.code == 'reconcile_completion_marker_missing'
        assert proxy.click_counts == [0]

@pytest.mark.asyncio
async def test_reconcile_accepts_turn_scoped_hidden_copy_control_chromium(tmp_path):
    """Hidden hover-toolbar button still signals a finished, idle last turn."""
    executable = find_chromium_executable()
    if not executable: pytest.skip('No Chromium executable')
    pytest.importorskip('playwright')
    html = HTML.replace('<button aria-label="Copy">Copy</button>',
                        '<button aria-label="Copy" style="display:none">Copy</button>')
    browser = BrowserManager(BrowserConfig(profile_dir=tmp_path/'browser'), backend=LocalContentBackend(executable, html))
    async with browser:
        proxy = ReadOnlyBrowser(browser)
        provider = ChatGPTWebProvider(proxy, url=ORIGIN+'/', timeout_s=2, poll_s=.04,
                    stable_observations=2, enable_conversation_continuation=True, enable_project_messages=True)
        result = await provider.reconcile_project_turn(project_id=PROJECT_ID, project_name='OpenAgent',
                  conversation_url=URL, user_message=PROMPT)
        assert result.content == ANSWER
        assert proxy.click_counts == [0]


@pytest.mark.asyncio
async def test_reconcile_rejects_assistant_before_user_chromium(tmp_path):
    executable = find_chromium_executable()
    if not executable: pytest.skip('No Chromium executable')
    pytest.importorskip('playwright')
    html = HTML.replace(f'<main><article data-turn-id="user-1"><div data-message-author-role="user">{PROMPT}</div></article>',
                        '<main>')
    html = html.replace('</main><form>', f'<article data-turn-id="user-1"><div data-message-author-role="user">{PROMPT}</div></article></main><form>')
    browser = BrowserManager(BrowserConfig(profile_dir=tmp_path/'browser'), backend=LocalContentBackend(executable, html))
    async with browser:
        proxy = ReadOnlyBrowser(browser)
        provider = ChatGPTWebProvider(proxy, url=ORIGIN+'/', timeout_s=1.1, poll_s=.03,
                    stable_observations=2, enable_conversation_continuation=True, enable_project_messages=True)
        with pytest.raises(BrowserUIError) as exc:
            await provider.reconcile_project_turn(project_id=PROJECT_ID, project_name='OpenAgent',
                conversation_url=URL, user_message=PROMPT)
        assert exc.value.code == 'reconcile_assistant_not_after_user'
        assert proxy.click_counts == [0]

@pytest.mark.asyncio
async def test_reconcile_user_article_role_fallback_chromium(tmp_path):
    """Actual ChatGPT can mark the ARTICLE rather than a nested user div."""
    executable = find_chromium_executable()
    if not executable: pytest.skip('No Chromium executable')
    pytest.importorskip('playwright')
    article = (f'<article data-turn="user" data-testid="conversation-turn-0">'
               f'<div class="user-message-bubble-color"><div class="whitespace-pre-wrap">{PROMPT}</div></div>'
               '<button>Edit</button></article>')
    html = HTML.replace(f'<article data-turn-id="user-1"><div data-message-author-role="user">{PROMPT}</div></article>', article)
    browser = BrowserManager(BrowserConfig(profile_dir=tmp_path/'browser'), backend=LocalContentBackend(executable, html))
    async with browser:
        proxy = ReadOnlyBrowser(browser)
        provider = ChatGPTWebProvider(proxy, url=ORIGIN+'/', timeout_s=2, poll_s=.04,
                    stable_observations=2, enable_conversation_continuation=True,enable_project_messages=True)
        result = await provider.reconcile_project_turn(project_id=PROJECT_ID, project_name='OpenAgent',
                    conversation_url=URL, user_message=PROMPT)
        assert result.content == ANSWER
        assert proxy.click_counts == [0]


@pytest.mark.asyncio
async def test_reconcile_turn_key_bubble_and_grouped_copy_chromium(tmp_path):
    """A newer renderer groups the prompt and assistant under the same turn-key."""
    executable = find_chromium_executable()
    if not executable: pytest.skip('No Chromium executable')
    pytest.importorskip('playwright')
    html = HTML.replace(f'<article data-turn-id="user-1"><div data-message-author-role="user">{PROMPT}</div></article>',
        f'<div data-turn-key="k1"><div data-user-message-bubble>{PROMPT}</div>')
    html = html.replace('<article data-turn-id="assistant-1"><div data-message-author-role="assistant">',
                        '<div data-conversation-role="assistant">')
    html = html.replace('<button aria-label="Copy">Copy</button></article>',
                        '<button aria-label="Copy">Copy</button></div>')
    browser = BrowserManager(BrowserConfig(profile_dir=tmp_path/'browser'), backend=LocalContentBackend(executable, html))
    async with browser:
        proxy = ReadOnlyBrowser(browser)
        provider = ChatGPTWebProvider(proxy, url=ORIGIN+'/', timeout_s=2, poll_s=.04,
                    stable_observations=2, enable_conversation_continuation=True,enable_project_messages=True)
        result = await provider.reconcile_project_turn(project_id=PROJECT_ID, project_name='OpenAgent',
                        conversation_url=URL, user_message=PROMPT)
        assert result.content == ANSWER
        assert proxy.click_counts == [0]


@pytest.mark.asyncio
async def test_reconcile_grouped_copy_before_answer_not_proof_chromium(tmp_path):
    """A Copy control on the prompt must never prove assistant completion."""
    executable = find_chromium_executable()
    if not executable: pytest.skip('No Chromium executable')
    pytest.importorskip('playwright')
    html = HTML.replace(f'<article data-turn-id="user-1"><div data-message-author-role="user">{PROMPT}</div></article>',
        f'<div data-turn-key="k1"><div data-user-message-bubble>{PROMPT}</div><button aria-label="Copy">Copy</button>')
    html = html.replace('<article data-turn-id="assistant-1"><div data-message-author-role="assistant">',
                        '<div data-conversation-role="assistant">')
    html = html.replace('<button aria-label="Copy">Copy</button></article>', '</div>')
    browser = BrowserManager(BrowserConfig(profile_dir=tmp_path/'browser'), backend=LocalContentBackend(executable, html))
    async with browser:
        proxy = ReadOnlyBrowser(browser)
        provider = ChatGPTWebProvider(proxy, url=ORIGIN+'/', timeout_s=1, poll_s=.04,
                    stable_observations=2, enable_conversation_continuation=True,enable_project_messages=True)
        with pytest.raises(BrowserUIError) as exc:
            await provider.reconcile_project_turn(project_id=PROJECT_ID, project_name='OpenAgent',
                    conversation_url=URL, user_message=PROMPT)
        assert exc.value.code == 'reconcile_completion_marker_missing'
        assert proxy.click_counts == [0]


@pytest.mark.asyncio
async def test_reconcile_does_not_search_unrelated_page_text_chromium(tmp_path):
    """Sidebar matching prompt is not a role-marked user chat turn."""
    executable = find_chromium_executable()
    if not executable: pytest.skip('No Chromium executable')
    pytest.importorskip('playwright')
    html = HTML.replace(f'<article data-turn-id="user-1"><div data-message-author-role="user">{PROMPT}</div></article>', '')
    html = html.replace('<main>',f'<aside>{PROMPT}</aside><main>')
    browser = BrowserManager(BrowserConfig(profile_dir=tmp_path/'browser'), backend=LocalContentBackend(executable, html))
    async with browser:
        proxy = ReadOnlyBrowser(browser)
        provider = ChatGPTWebProvider(proxy, url=ORIGIN+'/', timeout_s=1, poll_s=.04,
                    stable_observations=2, enable_conversation_continuation=True,enable_project_messages=True)
        with pytest.raises(BrowserUIError) as exc:
            await provider.reconcile_project_turn(project_id=PROJECT_ID, project_name='OpenAgent',
                    conversation_url=URL, user_message=PROMPT)
        assert exc.value.code == 'reconcile_user_turn_missing'
        assert proxy.click_counts == [0]


@pytest.mark.asyncio
async def test_recovery_dom_census_read_only_and_no_text_chromium(tmp_path):
    executable = find_chromium_executable()
    if not executable: pytest.skip('No Chromium executable')
    pytest.importorskip('playwright')
    browser = BrowserManager(BrowserConfig(profile_dir=tmp_path/'browser'), backend=LocalContentBackend(executable, HTML))
    async with browser:
        proxy = ReadOnlyBrowser(browser)
        provider = ChatGPTWebProvider(proxy, url=ORIGIN+'/', timeout_s=2, poll_s=.03,
                    enable_conversation_continuation=True, enable_project_messages=True)
        census = await provider.inspect_project_conversation_dom(project_id=PROJECT_ID, conversation_url=URL)
        assert census['route_verified'] is True
        assert census['markers']['user_role'] == 1
        assert census['markers']['assistant_role'] == 1
        assert census['markers']['copy_actions'] == 1
        assert PROMPT not in str(census) and ANSWER not in str(census) and CID not in str(census)
        assert census['submitted'] is False and census['journal_changed'] is False
        assert proxy.click_counts == [0]
        with pytest.raises(BrowserUIError) as exc:
            await provider.inspect_project_conversation_dom(project_id=OTHER_ID, conversation_url=URL)
        assert exc.value.code == 'reconcile_invalid_input'
        assert proxy.click_counts == [0]

@pytest.mark.asyncio
async def test_reconcile_finds_answer_after_screen_reader_assistant_label_chromium(tmp_path):
    """Live-shape regression: 'ChatGPT said:' is NOT the reply content.

    The role marker acts as an accessibility heading; the actual visible
    markdown follows it as a sibling in the same logical turn-key wrapper.
    """
    executable = find_chromium_executable()
    if not executable: pytest.skip('No Chromium executable')
    pytest.importorskip('playwright')
    html = f'''<!doctype html><html><body><main>
    <div data-turn-key="turn-1">
      <div data-user-message-bubble>{PROMPT}</div>
      <div data-conversation-role="assistant">ChatGPT said:</div>
      <div data-chatgpt-agent-turn-start></div>
      <div class="markdown prose">{ANSWER}</div>
      <button aria-label="Copy">Copy</button>
    </div></main><script>window.clicked=0</script></body></html>'''
    browser = BrowserManager(BrowserConfig(profile_dir=tmp_path/'browser'),
                             backend=LocalContentBackend(executable, html))
    async with browser:
        proxy = ReadOnlyBrowser(browser)
        provider = ChatGPTWebProvider(proxy, url=ORIGIN+'/', timeout_s=2, poll_s=.03,
                    stable_observations=2, enable_conversation_continuation=True, enable_project_messages=True)
        result = await provider.reconcile_project_turn(project_id=PROJECT_ID,
                project_name='OpenAgent', conversation_url=URL, user_message=PROMPT)
        assert result.content == ANSWER
        assert result.content != 'ChatGPT said:'
        assert proxy.click_counts == [0]


@pytest.mark.asyncio
async def test_reconcile_rejects_role_only_label_without_answer_chromium(tmp_path):
    executable = find_chromium_executable()
    if not executable: pytest.skip('No Chromium executable')
    pytest.importorskip('playwright')
    html = f'''<!doctype html><html><body><main>
    <div data-turn-key="turn-1"><div data-user-message-bubble>{PROMPT}</div>
    <div data-conversation-role="assistant">ChatGPT said:</div>
    <button aria-label="Copy">Copy</button></div></main>
    <script>window.clicked=0</script></body></html>'''
    browser = BrowserManager(BrowserConfig(profile_dir=tmp_path/'browser'),
                             backend=LocalContentBackend(executable, html))
    async with browser:
        proxy = ReadOnlyBrowser(browser)
        provider = ChatGPTWebProvider(proxy, url=ORIGIN+'/', timeout_s=1, poll_s=.03,
                    stable_observations=2, enable_conversation_continuation=True, enable_project_messages=True)
        with pytest.raises(BrowserUIError) as exc:
            await provider.reconcile_project_turn(project_id=PROJECT_ID,
                    project_name='OpenAgent', conversation_url=URL, user_message=PROMPT)
        assert exc.value.code == 'reconcile_assistant_content_unverified'
        assert proxy.click_counts == [0]


@pytest.mark.asyncio
async def test_reconcile_rejects_previous_user_markdown_in_shared_turn_chromium(tmp_path):
    executable = find_chromium_executable()
    if not executable: pytest.skip('No Chromium executable')
    pytest.importorskip('playwright')
    html = f'''<!doctype html><html><body><main>
    <div data-turn-key="turn-1"><div data-user-message-bubble>{PROMPT}</div>
    <div class="markdown">Older unrelated prompt markdown</div>
    <div data-conversation-role="assistant">ChatGPT said:</div>
    <button aria-label="Copy">Copy</button></div></main>
    <script>window.clicked=0</script></body></html>'''
    browser = BrowserManager(BrowserConfig(profile_dir=tmp_path/'browser'),
                             backend=LocalContentBackend(executable, html))
    async with browser:
        proxy = ReadOnlyBrowser(browser)
        provider = ChatGPTWebProvider(proxy, url=ORIGIN+'/', timeout_s=1, poll_s=.03,
                    stable_observations=2, enable_conversation_continuation=True, enable_project_messages=True)
        with pytest.raises(BrowserUIError) as exc:
            await provider.reconcile_project_turn(project_id=PROJECT_ID,
                    project_name='OpenAgent', conversation_url=URL, user_message=PROMPT)
        assert exc.value.code == 'reconcile_assistant_content_unverified'
        assert proxy.click_counts == [0]

@pytest.mark.asyncio
async def test_live_answer_wait_ignores_heading_and_reads_later_sibling_content_chromium(tmp_path):
    """The normal post-Send path must not cache 'ChatGPT said:' either."""
    executable = find_chromium_executable()
    if not executable: pytest.skip('No Chromium executable')
    pytest.importorskip('playwright')
    html = f'''<!doctype html><html><body><main id="history">
    <div data-turn-key="k0"><div data-conversation-role="assistant" data-turn-id="old">Old answer</div></div>
    </main><script>
    window.setTimeout(() => {{
      const turn=document.createElement('div');turn.setAttribute('data-turn-key','k1');
      turn.innerHTML='<div data-conversation-role="assistant" data-turn-id="new">ChatGPT said:</div>'+
        '<div data-chatgpt-agent-turn-start></div><div class="markdown">{ANSWER}</div>'+
        '<button aria-label="Copy">Copy</button>';
      document.querySelector('#history').appendChild(turn);
    }}, 180);
    </script></body></html>'''
    browser = BrowserManager(BrowserConfig(profile_dir=tmp_path/'browser'),
                             backend=LocalContentBackend(executable, html))
    async with browser:
        provider = ChatGPTWebProvider(browser, url=ORIGIN+'/', timeout_s=2,
            poll_s=.04, stable_observations=2)
        page = await browser.open_tab('live-reply-fixture', URL)
        baseline = await provider._capture_baseline(page)
        result = await provider._wait_for_answer(page, baseline, False)
        assert result == ANSWER
        await browser.close_tab('live-reply-fixture')
