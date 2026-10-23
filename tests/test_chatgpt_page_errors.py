"""Synthetic browser tests for ChatGPT chat-unavailable pages and control inventory.

All tests use disposable Chromium profiles and injected HTML, never live accounts.
"""
import json
import pytest

from openagent.browser import BrowserConfig, BrowserManager
from openagent.providers.browser_ui import BrowserUIError
from openagent.providers.chatgpt_page_state import (
    _classify, chatgpt_page_error, chatgpt_feature_inventory,
)
from openagent.providers.chatgpt_web import ChatGPTWebProvider
from test_project_reconcile_browser import ORIGIN, PROJECT_ID, PROMPT, URL, ReadOnlyBrowser
from test_ui_real_dom import LocalContentBackend, find_chromium_executable


@pytest.mark.parametrize('label,expected', [
    ('You cannot access this chat', 'conversation_access_denied'),
    ('You can not access this chat.', 'conversation_access_denied'),
    ("You can't access this chat", 'conversation_access_denied'),
    ("You don't have access to this chat", 'conversation_access_denied'),
    ('This chat is not available', 'conversation_unavailable'),
    ("This chat isn't available", 'conversation_unavailable'),
    ('Chat not found', 'conversation_unavailable'),
    ('Unable to load conversation', 'conversation_unavailable'),
    ('OpenAgent Project test received.', None),
    ('How can I help you?', None),
])
def test_page_error_classifies_only_recognized_copy(label, expected):
    assert _classify(label) == expected


@pytest.mark.asyncio
async def test_chatgpt_error_and_controls_real_chromium(tmp_path):
    executable = find_chromium_executable()
    if not executable:
        pytest.skip('No Chromium executable')
    pytest.importorskip('playwright')
    cases = [
        ('denied', '<main><h1>You cannot access this chat</h1></main>', 'conversation_access_denied'),
        ('missing', '<main><div role="alert">This chat is not available</div></main>', 'conversation_unavailable'),
        ('plain', '<main>You can not access this chat</main>', 'conversation_access_denied'),
        ('quoted', '<main><article data-turn-key="t1"><div data-user-message-bubble>This chat is not available</div></article></main>', None),
        ('none', '<main><h1>Project not verified</h1></main>', None),
        ('controls', '''<main><button data-testid="model-switcher-dropdown-button">Model</button>
           <button data-testid="effort-picker">Thinking</button>
           <button aria-label="Attach files">+</button>
           <input type="file" hidden>
           <button aria-label="Voice">Voice</button>
           <button aria-label="Tools">Tools</button></main>''', None),
    ]
    for name, inner, expected in cases:
        async with BrowserManager(BrowserConfig(profile_dir=tmp_path/name),
                                  backend=LocalContentBackend(executable, '<!doctype html><html><body>'+inner+'</body></html>')) as browser:
            page = await browser.open_tab('fixture', ORIGIN + '/')
            assert await chatgpt_page_error(page) == expected
            if name == 'controls':
                snapshot = await chatgpt_feature_inventory(page)
                assert all(v == 'detected' for v in snapshot.values()), snapshot
                assert 'OpenAgent' not in json.dumps(snapshot)
            await browser.close_tab('fixture')


@pytest.mark.asyncio
async def test_repair_preflight_rejects_access_denied_before_route_mismatch(tmp_path):
    binary = find_chromium_executable()
    if not binary: pytest.skip('No Chromium')
    pytest.importorskip('playwright')
    html = '<!doctype html><html><body><main><h1>You cannot access this chat</h1></main><script>window.clicked=0</script></body></html>'
    browser = BrowserManager(BrowserConfig(profile_dir=tmp_path/'denied'),
                             backend=LocalContentBackend(binary, html))
    async with browser:
        proxy = ReadOnlyBrowser(browser, redirect=ORIGIN+'/')
        provider = ChatGPTWebProvider(proxy, url=ORIGIN+'/', timeout_s=1.2, poll_s=.03,
            enable_conversation_continuation=True, enable_project_messages=True)
        with pytest.raises(BrowserUIError) as error:
            await provider.reconcile_project_turn(project_id=PROJECT_ID, project_name='OpenAgent',
                conversation_url=URL, user_message=PROMPT)
        assert error.value.code == 'conversation_access_denied'
        assert proxy.click_counts == [0]


@pytest.mark.asyncio
async def test_delayed_error_redirect_is_not_reported_as_route_mismatch(tmp_path):
    binary = find_chromium_executable()
    if not binary: pytest.skip('No Chromium')
    pytest.importorskip('playwright')
    html = '''<!doctype html><html><body><main id="stage"></main>
      <script>setTimeout(() => document.querySelector('#stage').innerHTML =
      '<h1>This chat is not available</h1>', 140); window.clicked=0</script></body></html>'''
    browser = BrowserManager(BrowserConfig(profile_dir=tmp_path/'delayed'),
                             backend=LocalContentBackend(binary, html))
    async with browser:
        proxy = ReadOnlyBrowser(browser, redirect=ORIGIN+'/')
        provider = ChatGPTWebProvider(proxy, url=ORIGIN+'/', timeout_s=1.2, poll_s=.03,
            enable_conversation_continuation=True, enable_project_messages=True)
        with pytest.raises(BrowserUIError) as error:
            await provider.reconcile_project_turn(project_id=PROJECT_ID, project_name='OpenAgent',
                conversation_url=URL, user_message=PROMPT)
        assert error.value.code == 'conversation_unavailable'
        assert proxy.click_counts == [0]

@pytest.mark.asyncio
@pytest.mark.parametrize('reason', ['conversation_access_denied', 'conversation_unavailable'])
async def test_api_recovery_exposes_only_finite_page_error_and_preserves_journal(tmp_path, reason):
    """An unavailable remote page is NOT a reason to retry or repair locally."""
    from httpx import ASGITransport, AsyncClient
    from openagent.api import create_app
    from openagent.models import Message
    from openagent.registry import Registry
    from openagent.store import SessionStore
    from test_project_reconcile import RecoveryProvider

    class Unavailable(RecoveryProvider):
        async def reconcile_project_turn(self, **kwargs):
            raise BrowserUIError(reason, 'internal private remote page content')

    provider = Unavailable()
    registry = Registry(); registry.register(provider)
    store = SessionStore(tmp_path/'blocked.sqlite')
    sid = store.create('chatgpt-web/default')['id']
    store.bind_verified_project(sid, PROJECT_ID, 'OpenAgent')
    user = Message(role='user', content=PROMPT)
    store.claim_turn(sid, 'unknown-1', user, expected_project_id=PROJECT_ID)
    store.fail_turn(sid, 'unknown-1')
    async with AsyncClient(transport=ASGITransport(app=create_app(registry, store)),
                           base_url='http://test') as client:
        response = await client.post(
            f'/openagent/sessions/{sid}/turns/unknown-1/reconcile-check',
            json={'message': user.model_dump(mode='json'), 'conversation_url': URL},
        )
        assert response.status_code == 200
        assert response.json() == {'verifiable': False, 'reason': reason,
                                   'submitted': False, 'journal_changed': False}
        assert 'internal private' not in response.text
    assert store.list_turns(sid)[0]['status'] == 'unknown'
    assert store.get(sid)['messages'] == []
    store.close()
