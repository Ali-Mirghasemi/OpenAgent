"""Experimental conversation binding: offline synthetic pages only.

No ChatGPT network traffic, account data, or cookies are accessed by these tests.
"""
import asyncio
import pytest
from httpx import AsyncClient, ASGITransport

from openagent.api import create_app
from openagent.browser import BrowserConfig, BrowserManager
from openagent.models import Message
from openagent.providers.browser_ui import BrowserUIError
from openagent.providers.chatgpt_web import (
    CHATGPT_MODEL, CHATGPT_URL, ChatGPTWebProvider,
    chatgpt_conversation_url, parse_chatgpt_conversation_id,
)
from openagent.registry import Registry
from openagent.store import SessionStore
from test_chatgpt_web import Page, Backend

CHAT_ID = 'c95b50db-0202-4c2b-a146-d7305d96e132'
OTHER_ID = 'eafb6a85-0e95-454a-bb3b-a3d79e9ca0f4'


@pytest.mark.parametrize('url', [
    'http://chatgpt.com/c/' + CHAT_ID,
    'https://chatgpt.com.evil.example/c/' + CHAT_ID,
    'https://evil.example/c/' + CHAT_ID,
    'https://chatgpt.com/share/' + CHAT_ID,
    'https://chatgpt.com/g/example/c/' + CHAT_ID,
    'https://chatgpt.com/c/' + CHAT_ID + '/extra',
    'https://chatgpt.com/c/' + CHAT_ID + '?token=secret',
    'https://chatgpt.com/c/' + CHAT_ID + '#frag',
    'https://chatgpt.com/c/not-a-uuid',
    'https://chatgpt.com/c/' + CHAT_ID.upper(),
    'https://user:pass@chatgpt.com/c/' + CHAT_ID,
    'https://chatgpt.com/',
])
def test_only_trusted_canonical_chatgpt_conversation_urls(url):
    assert parse_chatgpt_conversation_id(url) is None


def test_opaque_identity_can_only_create_strict_chatgpt_route():
    assert parse_chatgpt_conversation_id(CHATGPT_URL + 'c/' + CHAT_ID) == CHAT_ID
    assert chatgpt_conversation_url('https://chatgpt.com', CHAT_ID) == CHATGPT_URL + 'c/' + CHAT_ID
    with pytest.raises(BrowserUIError, match='invalid upstream'):
        chatgpt_conversation_url('https://chatgpt.com', '../share')


class RoutePage(Page):
    def __init__(self, *, route_mode='correct'):
        super().__init__('success')
        self.route_mode = route_mode
        self.url = CHATGPT_URL

    async def goto(self, url, *args, **kwargs):
        self.url = url
        if self.route_mode == 'redirect' and '/c/' in url:
            self.url = CHATGPT_URL

    async def respond(self):
        await super().respond()
        if self.route_mode == 'missing_route':
            self.url = CHATGPT_URL
        elif self.route_mode == 'wrong_route':
            self.url = CHATGPT_URL + 'c/' + OTHER_ID
        else:
            self.url = CHATGPT_URL + 'c/' + CHAT_ID


class RouteBackend(Backend):
    def __init__(self, route_mode='correct'):
        super().__init__('success')
        self.route_mode = route_mode

    async def new_page(self):
        p = RoutePage(route_mode=self.route_mode)
        self.pages.append(p)
        return p


def provider_setup(tmp_path, *, enable=True, route_mode='correct'):
    backend = RouteBackend(route_mode)
    browser = BrowserManager(BrowserConfig(profile_dir=tmp_path / 'profile'), backend=backend)
    p = ChatGPTWebProvider(browser, enable_conversation_continuation=enable,
                           timeout_s=1.0, poll_s=0.02, stable_observations=2)
    return p, browser, backend


@pytest.mark.asyncio
async def test_continuation_requires_explicit_opt_in(tmp_path):
    provider, browser, backend = provider_setup(tmp_path, enable=False)
    assert not provider.supports_upstream_conversations
    with pytest.raises(RuntimeError, match='not implemented'):
        await provider.complete_in_session(CHATGPT_MODEL, [Message(role='user', content='x')],
                                           upstream_conversation_id=CHAT_ID)
    assert backend.pages == []
    await browser.stop()


@pytest.mark.asyncio
async def test_new_chat_binds_and_follow_up_only_sends_new_text(tmp_path):
    provider, browser, backend = provider_setup(tmp_path)
    first = await provider.complete_in_session(
        CHATGPT_MODEL, [Message(role='user', content='first question')],
        upstream_conversation_id=None,
    )
    assert first.upstream_conversation_id == CHAT_ID
    assert 'first question' in backend.pages[0].filled
    assert backend.pages[0].clicks == 1
    follow = await provider.complete_in_session(
        CHATGPT_MODEL, [Message(role='user', content='first question'),
                        Message(role='assistant', content='earlier answer'),
                        Message(role='user', content='second question')],
        upstream_conversation_id=CHAT_ID,
    )
    assert follow.upstream_conversation_id == CHAT_ID
    assert backend.pages[1].filled == 'second question'
    assert backend.pages[1].url == CHATGPT_URL + 'c/' + CHAT_ID
    assert backend.pages[1].clicks == 1
    assert all(p.closed for p in backend.pages)
    await browser.stop()


@pytest.mark.asyncio
async def test_redirected_existing_conversation_never_submits(tmp_path):
    provider, browser, backend = provider_setup(tmp_path, route_mode='redirect')
    with pytest.raises(BrowserUIError) as err:
        await provider.complete_in_session(
            CHATGPT_MODEL, [Message(role='user', content='followup')],
            upstream_conversation_id=CHAT_ID,
        )
    assert err.value.code == 'conversation_mismatch'
    assert backend.pages[0].clicks == 0
    assert backend.pages[0].closed
    await browser.stop()


@pytest.mark.asyncio
async def test_missing_url_after_click_is_uncertain_and_no_retry(tmp_path):
    provider, browser, backend = provider_setup(tmp_path, route_mode='missing_route')
    with pytest.raises(BrowserUIError) as err:
        await provider.complete_in_session(
            CHATGPT_MODEL, [Message(role='user', content='first')], upstream_conversation_id=None,
        )
    assert err.value.code == 'remote_outcome_unknown'
    assert backend.pages[0].clicks == 1
    assert backend.pages[0].closed
    await browser.stop()


@pytest.mark.asyncio
async def test_changed_upstream_url_after_click_is_uncertain(tmp_path):
    provider, browser, backend = provider_setup(tmp_path, route_mode='wrong_route')
    with pytest.raises(BrowserUIError) as err:
        await provider.complete_in_session(
            CHATGPT_MODEL, [Message(role='user', content='followup')],
            upstream_conversation_id=CHAT_ID,
        )
    assert err.value.code == 'remote_outcome_unknown'
    assert backend.pages[0].clicks == 1
    assert backend.pages[0].closed
    await browser.stop()


@pytest.mark.asyncio
async def test_reject_invalid_session_message_without_tab_or_submission(tmp_path):
    provider, browser, backend = provider_setup(tmp_path)
    with pytest.raises(BrowserUIError) as err:
        await provider.complete_in_session(CHATGPT_MODEL, [Message(role='tool', content='x')],
                                           upstream_conversation_id=CHAT_ID)
    assert err.value.code == 'unsupported_message'
    assert backend.pages == []
    await browser.stop()


@pytest.mark.asyncio
async def test_http_session_lifecycle_and_restart_with_chatgpt_route_fixture(tmp_path):
    provider, browser, backend = provider_setup(tmp_path)
    reg = Registry()
    reg.register(provider)
    path = tmp_path / 'durable.db'
    db = SessionStore(path)
    app = create_app(reg, db)
    async with AsyncClient(transport=ASGITransport(app=app), base_url='http://test') as cli:
        sid = (await cli.post('/openagent/sessions', json={'model': CHATGPT_MODEL})).json()['id']
        first = await cli.post(f'/openagent/sessions/{sid}/turns', json={
            'request_id': 'one', 'message': {'role': 'user', 'content': 'first'},
        })
        assert first.status_code == 200
        assert first.json()['output']['upstream_conversation_id'] == CHAT_ID
        assert (await cli.get(f'/openagent/sessions/{sid}')).json()['upstream']['conversation_id'] == CHAT_ID
    db.close()
    db = SessionStore(path)
    app = create_app(reg, db)
    async with AsyncClient(transport=ASGITransport(app=app), base_url='http://test') as cli:
        second = await cli.post(f'/openagent/sessions/{sid}/turns', json={
            'request_id': 'two', 'message': {'role': 'user', 'content': 'second'},
        })
        assert second.status_code == 200
        assert second.json()['output']['upstream_conversation_id'] == CHAT_ID
        assert backend.pages[1].filled == 'second'
        assert backend.pages[1].clicks == 1
        cached = await cli.post(f'/openagent/sessions/{sid}/turns', json={
            'request_id': 'two', 'message': {'role': 'user', 'content': 'second'},
        })
        assert cached.status_code == 200 and cached.json()['cached']
        assert len(backend.pages) == 2
    db.close()
    await browser.stop()


@pytest.mark.asyncio
async def test_unconfirmed_post_submit_route_blocks_next_turn_in_journal(tmp_path):
    provider, browser, backend = provider_setup(tmp_path, route_mode='missing_route')
    registry = Registry()
    registry.register(provider)
    db = SessionStore(tmp_path / 'store.db')
    app = create_app(registry, db)
    async with AsyncClient(transport=ASGITransport(app=app), base_url='http://test') as cli:
        sid = (await cli.post('/openagent/sessions', json={'model': CHATGPT_MODEL})).json()['id']
        first = await cli.post(f'/openagent/sessions/{sid}/turns', json={
            'request_id': '1', 'message': {'role': 'user', 'content': 'first'},
        })
        assert first.status_code == 502
        second = await cli.post(f'/openagent/sessions/{sid}/turns', json={
            'request_id': '2', 'message': {'role': 'user', 'content': 'second'},
        })
        assert second.status_code == 409
        ledger = (await cli.get(f'/openagent/sessions/{sid}/turns')).json()['turns']
        assert [r['status'] for r in ledger] == ['unknown']
        assert len(backend.pages) == 1 and backend.pages[0].clicks == 1
    db.close()
    await browser.stop()

@pytest.mark.asyncio
async def test_resumed_page_without_visible_history_is_never_submitted(tmp_path):
    from test_chatgpt_web import Locator

    class NoHistoryPage(RoutePage):
        def locator(self, selector):
            if selector == self_sel:
                class Empty(Locator):
                    async def count(self):
                        return 0
                return Empty(self, selector)
            return super().locator(selector)

    from openagent.providers.chatgpt_web import CHATGPT_SELECTORS
    self_sel = CHATGPT_SELECTORS.assistant_message

    class NoHistoryBackend(RouteBackend):
        async def new_page(self):
            page = NoHistoryPage()
            self.pages.append(page)
            return page

    backend = NoHistoryBackend()
    browser = BrowserManager(BrowserConfig(profile_dir=tmp_path/'no-history'), backend=backend)
    provider = ChatGPTWebProvider(browser, timeout_s=1.0, poll_s=.02,
                                  stable_observations=2, enable_conversation_continuation=True)
    with pytest.raises(BrowserUIError) as err:
        await provider.complete_in_session(
            CHATGPT_MODEL, [Message(role='user', content='second')], upstream_conversation_id=CHAT_ID,
        )
    assert err.value.code == 'conversation_unverified'
    assert backend.pages[0].clicks == 0
    await browser.stop()


def test_route_builder_rejects_non_chatgpt_remote_origin():
    with pytest.raises(BrowserUIError):
        chatgpt_conversation_url('https://evil.example', CHAT_ID)
