"""ChatGPT-specific adapter contract tests using synthetic UI; no live service."""
import asyncio
import pytest
from httpx import ASGITransport, AsyncClient

from openagent.api import create_app
from openagent.browser import BrowserConfig, BrowserManager
from openagent.capabilities import Feature
from openagent.models import Message
from openagent.providers.browser_ui import BrowserUIError
from openagent.providers.chatgpt_web import (
    CHATGPT_MODEL, CHATGPT_SELECTORS, CHATGPT_URL,
    ChatGPTWebProvider, TURN_COPY_BUTTON, TURN_PENDING,
)
from openagent.registry import Registry


class Locator:
    def __init__(self, page, selector, index=None):
        self.page, self.selector, self.index = page, selector, index

    @property
    def first(self):
        return self

    def nth(self, i):
        return Locator(self.page, self.selector, i)

    def locator(self, selector):
        return Locator(self.page, selector, self.index)

    async def count(self):
        if self.selector == CHATGPT_SELECTORS.assistant_message:
            return 1 + int(self.page.submitted)
        if self.selector == 'xpath=ancestor::article[1]':
            return 1
        if self.selector == TURN_PENDING:
            return int(self.page.pending)
        if self.selector == '.markdown':
            return 1
        return 0

    async def is_visible(self):
        if self.selector == CHATGPT_SELECTORS.generation_in_progress:
            return self.page.busy
        if self.selector == CHATGPT_SELECTORS.response_complete:
            return True  # Stale global copy button belongs to OLD turn.
        if self.selector == TURN_COPY_BUTTON:
            return self.page.done
        if self.selector == CHATGPT_SELECTORS.security_challenge:
            return self.page.challenge
        return self.selector in {CHATGPT_SELECTORS.composer, CHATGPT_SELECTORS.send_button}

    async def inner_text(self):
        return self.page.answer

    async def fill(self, text):
        self.page.filled = text

    async def click(self):
        self.page.submitted = True
        self.page.busy = True
        self.page.pending = True
        self.page.clicks += 1
        if self.page.behavior == 'success':
            self.page.reply_task = asyncio.create_task(self.page.respond())
        elif self.page.behavior == 'challenge':
            self.page.challenge = True


class Page:
    def __init__(self, behavior='success'):
        self.behavior = behavior
        self.submitted = False
        self.busy = False
        self.pending = False
        self.done = False
        self.challenge = False
        self.closed = False
        self.clicks = 0
        self.filled = ''
        self.answer = ''
        self.reply_task = None

    def is_closed(self):
        return self.closed

    def locator(self, selector):
        return Locator(self, selector)

    async def goto(self, *args, **kwargs):
        pass

    async def close(self):
        self.closed = True
        if self.reply_task and not self.reply_task.done():
            self.reply_task.cancel()

    async def respond(self):
        await asyncio.sleep(.02)
        self.answer = 'Partial text'
        await asyncio.sleep(.05)
        self.answer = 'Correct final answer'
        self.pending = False
        self.busy = False
        self.done = True


class Backend:
    def __init__(self, behavior='success'):
        self.behavior, self.pages = behavior, []
        self.connected = False
        self.starts = 0

    async def start(self, config):
        self.connected = True
        self.starts += 1

    def is_connected(self):
        return self.connected

    async def new_page(self):
        page = Page(self.behavior)
        self.pages.append(page)
        return page

    async def stop(self):
        self.connected = False


def setup(tmp_path, behavior='success', timeout=1.0):
    backend = Backend(behavior)
    browser = BrowserManager(BrowserConfig(profile_dir=tmp_path/'profile'), backend=backend)
    provider = ChatGPTWebProvider(browser, timeout_s=timeout, poll_s=.02, stable_observations=2)
    return provider, browser, backend


@pytest.mark.asyncio
async def test_explicit_default_selection_and_unknown_capabilities(tmp_path):
    provider, browser, backend = setup(tmp_path)
    assert await provider.models() == [CHATGPT_MODEL]
    caps = await provider.capabilities(CHATGPT_MODEL)
    assert caps.transport == 'browser'
    assert caps.reasoning_levels == []
    assert caps.native_tools.state == 'unsupported'
    assert caps.file_upload.state == 'unknown'
    assert caps.persistent_chat.state == 'unsupported'
    assert browser.status().state == 'stopped'
    with pytest.raises(ValueError):
        await provider.capabilities('chatgpt-web/gpt-unknown')


@pytest.mark.asyncio
async def test_read_only_inspection_starts_browser_without_click(tmp_path):
    provider, browser, backend = setup(tmp_path)
    observation = await provider.inspect()
    assert observation.as_dict()['state'] == 'ready'
    assert backend.starts == 1
    assert backend.pages[0].clicks == 0
    assert backend.pages[0].closed
    assert browser.status().open_tabs == 0
    await browser.stop()


@pytest.mark.asyncio
async def test_complete_requires_new_turn_copy_and_no_busy_marker(tmp_path):
    provider, browser, backend = setup(tmp_path)
    response = await provider.complete(CHATGPT_MODEL, [Message(role='user',content='test')])
    assert response.content == 'Correct final answer'
    assert backend.pages[0].clicks == 1
    assert 'test' in backend.pages[0].filled
    assert backend.pages[0].closed
    assert browser.status().open_tabs == 0
    await browser.stop()


@pytest.mark.asyncio
async def test_old_copy_does_not_mark_new_turn_complete(tmp_path):
    provider, browser, backend = setup(tmp_path, behavior='stuck', timeout=1.0)
    with pytest.raises(BrowserUIError) as error:
        await provider.complete(CHATGPT_MODEL, [Message(role='user', content='test')])
    assert error.value.code == 'generation_timeout'
    assert backend.pages[0].clicks == 1
    assert backend.pages[0].closed
    await browser.stop()


@pytest.mark.asyncio
async def test_challenge_fails_without_retry(tmp_path):
    provider, browser, backend = setup(tmp_path, behavior='challenge')
    with pytest.raises(BrowserUIError) as error:
        await provider.complete(CHATGPT_MODEL, [Message(role='user', content='test')])
    assert error.value.code == 'security_challenge'
    assert backend.pages[0].clicks == 1
    await browser.stop()


@pytest.mark.asyncio
async def test_native_tools_not_sent_and_no_new_page(tmp_path):
    provider, browser, backend = setup(tmp_path)
    with pytest.raises(BrowserUIError) as error:
        await provider.complete(CHATGPT_MODEL, [Message(role='user', content='test')],tools=[{'type':'function'}])
    assert error.value.code == 'unsupported_tools'
    assert backend.pages == []
    await browser.stop()


@pytest.mark.asyncio
async def test_adapter_opt_in_http_capabilities_and_diagnostics(tmp_path, store):
    provider, browser, backend = setup(tmp_path)
    registry=Registry();registry.register(provider)
    app=create_app(registry,store,browser_manager=browser)
    async with AsyncClient(transport=ASGITransport(app=app),base_url='http://test') as client:
        result = await client.get('/v1/models')
        assert result.json()['data'][0]['id'] == CHATGPT_MODEL
        caps = await client.get('/openagent/capabilities/chatgpt-web/default')
        assert caps.status_code == 200
        assert caps.json()['native_tools']['state'] == 'unsupported'
        assert (await client.get('/openagent/providers/chatgpt-web/diagnostics')).json()['state'] == 'ready'
        invalid = await client.post('/v1/chat/completions',json={
            'model':CHATGPT_MODEL,'messages':[{'role':'user','content':'test'}],
            'stream':True,
        })
        assert invalid.status_code == 422
    assert all(page.clicks == 0 for page in backend.pages)
    await browser.stop()


def test_chatgpt_origin_and_fixture_restrictions(tmp_path):
    browser = BrowserManager(BrowserConfig(profile_dir=tmp_path / 'p'))
    with pytest.raises(ValueError):
        ChatGPTWebProvider(browser,url='https://evil.example/')
    provider=ChatGPTWebProvider(browser)
    assert provider.site.url == CHATGPT_URL

@pytest.mark.asyncio
async def test_http_browser_provider_error_is_sanitized(tmp_path, store):
    provider, browser, backend = setup(tmp_path, behavior='challenge')
    registry=Registry(); registry.register(provider)
    app=create_app(registry,store,api_token='secret',browser_manager=browser)
    async with AsyncClient(transport=ASGITransport(app=app),base_url='http://test') as client:
        denied = await client.post('/v1/chat/completions',json={
            'model':CHATGPT_MODEL,'messages':[{'role':'user','content':'test'}],
        })
        assert denied.status_code == 401
        response = await client.post('/v1/chat/completions',headers={'Authorization':'Bearer secret'},json={
            'model':CHATGPT_MODEL,'messages':[{'role':'user','content':'test'}],
        })
        assert response.status_code == 503
        assert response.json()['error']['code'] == 'security_challenge'
        assert 'cookie' not in response.text.lower()
    assert backend.pages[0].clicks == 1
    await browser.stop()


def test_cli_requires_api_token_for_signed_in_browser(monkeypatch, tmp_path, capsys):
    from openagent import cli
    import sys
    monkeypatch.delenv('OPENAGENT_API_TOKEN', raising=False)
    monkeypatch.setattr(sys,'argv',['openagent','serve','--enable-chatgpt-web',
                                    '--database',str(tmp_path/'db.sqlite')])
    with pytest.raises(SystemExit) as exc:
        cli.main()
    assert exc.value.code == 2
    assert 'requires OPENAGENT_API_TOKEN' in capsys.readouterr().err

@pytest.mark.asyncio
async def test_app_lifespan_closes_browser_manager(tmp_path, store):
    provider, browser, backend = setup(tmp_path)
    registry = Registry(); registry.register(provider)
    app = create_app(registry, store, browser_manager=browser)
    async with app.router.lifespan_context(app):
        await browser.start()
        assert browser.status().state == 'running'
    assert browser.status().state == 'stopped'
    assert backend.connected is False
