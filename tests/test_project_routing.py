"""Project-scoped turn identity and API isolation. All synthetic, no account access."""
import uuid
from types import SimpleNamespace

import pytest
from httpx import ASGITransport, AsyncClient

from openagent.api import create_app
from openagent.capabilities import Capabilities
from openagent.models import Message, ProviderOutput
from openagent.providers.base import Provider
from openagent.providers.browser_ui import BrowserUIError
from openagent.providers.chatgpt_web import (
    CHATGPT_MODEL, ChatGPTWebProvider,
    parse_project_conversation_id, project_conversation_url,
)
from openagent.registry import Registry
from openagent.store import SessionBusy, SessionStore
from test_project_binding import PROJECT_ID, OTHER_ID

CID = str(uuid.UUID('12345678-1234-4123-8123-123456789abc'))
ORIGIN = 'https://chatgpt.com'


@pytest.mark.parametrize('url,valid', [
    (f'{ORIGIN}/g/{PROJECT_ID}/c/{CID}', True),
    (f'{ORIGIN}/g/{OTHER_ID}/c/{CID}', False),
    (f'{ORIGIN}/g/{PROJECT_ID}/project', False),
    (f'{ORIGIN}/c/{CID}', False),
    (f'{ORIGIN}/g/{PROJECT_ID}/c/{CID}?mode=foo', False),
    (f'{ORIGIN}/g/{PROJECT_ID}/c/{CID}#x', False),
    (f'{ORIGIN}/g/{PROJECT_ID}/c/{CID}/more', False),
    (f'https://evil.example/g/{PROJECT_ID}/c/{CID}', False),
    (f'{ORIGIN}/g/{PROJECT_ID}/c/not-a-uuid', False),
])
def test_project_route_requires_exact_project_origin_and_uuid(url, valid):
    assert (parse_project_conversation_id(url, PROJECT_ID) == CID) is valid


def test_project_route_builder_and_invalid_inputs():
    assert project_conversation_url(ORIGIN, PROJECT_ID, CID) == f'{ORIGIN}/g/{PROJECT_ID}/c/{CID}'
    for pid, uid in [('bad', CID), (PROJECT_ID, '../escape')]:
        with pytest.raises(BrowserUIError):
            project_conversation_url(ORIGIN, pid, uid)


class ProjectFixtureProvider(Provider):
    id = 'chatgpt-web'
    supports_upstream_conversations = True

    def __init__(self, enabled):
        self.supports_project_messages = enabled
        self.project_calls = []
        self.fail = False
        self.changed_id = False

    async def models(self): return [CHATGPT_MODEL]
    async def capabilities(self, model):
        return Capabilities(provider=self.id, model=model, transport='mock', authentication='none')

    async def complete(self, model, messages, tools=None):
        raise AssertionError('normal provider path invoked for Project')

    async def complete_in_session(self, model, messages, *, upstream_conversation_id):
        raise AssertionError('unscoped upstream session path invoked')

    async def complete_in_project_session(self, model, messages, *, project_id, project_name,
                                          upstream_conversation_id):
        assert model == CHATGPT_MODEL
        self.project_calls.append((project_id, project_name, upstream_conversation_id,
                                   [m.content for m in messages]))
        if self.fail:
            raise BrowserUIError('remote_outcome_unknown', 'browser turn ambiguous')
        return ProviderOutput(content=f'project reply {len(self.project_calls)}',
                              upstream_conversation_id=('different' if self.changed_id else CID))


@pytest.mark.asyncio
async def test_project_turn_requires_explicit_gate_and_preserves_binding_across_restart(tmp_path):
    path = tmp_path / 'project.sqlite'
    provider = ProjectFixtureProvider(enabled=False)
    store = SessionStore(path)
    sid = store.create(CHATGPT_MODEL)['id']
    store.bind_verified_project(sid, PROJECT_ID, 'OpenAgent')
    registry = Registry(); registry.register(provider)
    app = create_app(registry, store)
    url = f'/openagent/sessions/{sid}/turns'
    body = {'request_id': 'first', 'message': {'role': 'user', 'content': 'hello'}}
    async with AsyncClient(transport=ASGITransport(app=app), base_url='http://test') as client:
        response = await client.post(url, json=body)
        assert response.status_code == 422
        assert store.list_turns(sid) == []
        assert provider.project_calls == []
        provider.supports_project_messages = True
        response = await client.post(url, json=body)
        assert response.status_code == 200
        assert response.json()['output']['upstream_conversation_id'] == CID
        response = await client.post(url, json=body)
        assert response.status_code == 200 and response.json()['cached'] is True
        assert len(provider.project_calls) == 1
    store.close()
    store = SessionStore(path)
    assert store.get(sid)['project']['id'] == PROJECT_ID
    assert store.get(sid)['upstream']['conversation_id'] == CID
    app = create_app(registry, store)
    async with AsyncClient(transport=ASGITransport(app=app), base_url='http://test') as client:
        response = await client.post(url, json={'request_id': 'second', 'message': {'role': 'user', 'content': 'next'}})
        assert response.status_code == 200
        assert provider.project_calls[-1] == (PROJECT_ID, 'OpenAgent', CID, ['hello', 'project reply 1', 'next'])
        assert len(store.get(sid)['messages']) == 4
    store.close()


@pytest.mark.asyncio
async def test_project_changed_upstream_id_fails_closed_without_retry(tmp_path):
    provider = ProjectFixtureProvider(True)
    reg = Registry(); reg.register(provider)
    store = SessionStore(tmp_path/'db.sqlite')
    sid = store.create(CHATGPT_MODEL)['id']
    store.bind_verified_project(sid, PROJECT_ID, 'OpenAgent')
    url = f'/openagent/sessions/{sid}/turns'
    app = create_app(reg, store)
    async with AsyncClient(transport=ASGITransport(app=app), base_url='http://test') as client:
        first = await client.post(url, json={'request_id':'1','message':{'role':'user','content':'hi'}})
        assert first.status_code == 200
        provider.changed_id = True
        second_body = {'request_id':'2','message':{'role':'user','content':'next'}}
        second = await client.post(url, json=second_body)
        assert second.status_code == 502
        repeat = await client.post(url, json=second_body)
        assert repeat.status_code == 409
        assert len(provider.project_calls) == 2
        assert store.list_turns(sid)[-1]['status'] == 'unknown'
        assert store.get(sid)['upstream']['conversation_id'] == CID
    store.close()


def test_atomic_project_gate_rejects_mismatch_and_unscoped_turns(tmp_path):
    store = SessionStore(tmp_path/'db.sqlite')
    sid = store.create(CHATGPT_MODEL)['id']
    store.bind_verified_project(sid, PROJECT_ID, 'OpenAgent')
    msg = Message(role='user', content='should not send')
    for expected in (None, OTHER_ID):
        with pytest.raises(SessionBusy):
            store.claim_turn(sid, 'same', msg, expected_project_id=expected)
    assert store.list_turns(sid) == []
    assert store.claim_turn(sid, 'same', msg, expected_project_id=PROJECT_ID) is None
    store.fail_turn(sid, 'same')
    store.close()


def test_project_provider_rejects_opt_in_without_conversation_capability():
    with pytest.raises(ValueError, match='require'):
        ChatGPTWebProvider(SimpleNamespace(config=SimpleNamespace(allowed_origins=())), enable_project_messages=True)
