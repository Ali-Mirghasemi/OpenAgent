"""One-time correction of a bad already-reconciled label-only answer.

The remote provider is simulated; API checks never submit. Storage is real
SQLite and all checks are repeated transactionally before commit.
"""
import pytest
from httpx import ASGITransport, AsyncClient
from openagent.api import create_app
from openagent.models import Message, ProviderOutput
from openagent.registry import Registry
from openagent.store import SessionStore, SessionBusy
from openagent.providers.browser_ui import BrowserUIError
from test_project_binding import PROJECT_ID
from test_project_routing import CID
from test_project_reconcile import RecoveryProvider, SLUG_ROUTE

PROMPT = 'Reply with exactly: OpenAgent Project test received.'
ANSWER = 'OpenAgent Project test received.'


class RepairProvider(RecoveryProvider):
    def __init__(self):
        super().__init__()
        self.answer = ANSWER
        self.fail_extract = False

    async def reconcile_project_turn(self, *, project_id, project_name, conversation_url, user_message):
        self.recovery_calls.append((project_id, project_name, conversation_url, user_message))
        if self.fail_extract:
            raise BrowserUIError('reconcile_assistant_content_unverified', 'Role only')
        return ProviderOutput(content=self.answer, upstream_conversation_id=CID)


def seeded(tmp_path):
    store = SessionStore(tmp_path/'repair.sqlite')
    sid = store.create('chatgpt-web/default')['id']
    store.bind_verified_project(sid, PROJECT_ID, 'OpenAgent')
    user = Message(role='user', content=PROMPT)
    store.claim_turn(sid, 'project-test-001', user, expected_project_id=PROJECT_ID)
    store.complete_turn(sid, 'project-test-001', user,
        ProviderOutput(content='ChatGPT said:', upstream_conversation_id=CID),
        supports_upstream_conversations=True)
    return store, sid, user


@pytest.mark.asyncio
async def test_repair_checks_are_read_only_and_correction_is_atomic(tmp_path):
    store, sid, user = seeded(tmp_path)
    provider = RepairProvider()
    registry = Registry(); registry.register(provider)
    app = create_app(registry, store)
    root = f'/openagent/sessions/{sid}/turns/project-test-001'
    body = {'message': user.model_dump(mode='json'), 'conversation_url': SLUG_ROUTE}
    async with AsyncClient(transport=ASGITransport(app=app), base_url='http://test') as client:
        provider.fail_extract = True
        no = await client.post(root+'/repair-check', json=body)
        assert no.status_code == 200
        assert no.json()['reason'] == 'reconcile_assistant_content_unverified'
        assert store.get(sid)['messages'][-1]['content'] == 'ChatGPT said:'
        assert store.list_turns(sid)[0]['status'] == 'done'
        provider.fail_extract = False
        check = await client.post(root+'/repair-check', json=body)
        assert check.status_code == 200 and check.json()['correctable'] is True
        assert 'OpenAgent Project test received.' not in check.text
        assert store.get(sid)['messages'][-1]['content'] == 'ChatGPT said:'
        wrong = await client.post(root+'/repair', json={**body,'message':{'role':'user','content':'different'}})
        assert wrong.status_code == 409
        assert len(provider.recovery_calls) == 2
        ok = await client.post(root+'/repair', json=body)
        assert ok.status_code == 200 and ok.json()['repaired'] is True
        assert ok.json()['output']['content'] == ANSWER
        assert store.get(sid)['messages'][-1]['content'] == ANSWER
        assert store.get(sid)['upstream']['conversation_id'] == CID
        assert store.db.execute('SELECT COUNT(*) FROM turn_corrections').fetchone()[0] == 1
        replay = await client.post(f'/openagent/sessions/{sid}/turns', json={
            'request_id':'project-test-001','message':user.model_dump(mode='json')})
        assert replay.status_code == 200 and replay.json()['cached'] is True
        assert replay.json()['output']['content'] == ANSWER
        repeat = await client.post(root+'/repair', json=body)
        assert repeat.status_code == 409
    store.close()
    reopened = SessionStore(tmp_path/'repair.sqlite')
    assert reopened.get(sid)['messages'][-1]['content'] == ANSWER
    assert reopened.db.execute('SELECT COUNT(*) FROM turn_corrections').fetchone()[0] == 1
    reopened.close()


@pytest.mark.asyncio
async def test_repair_fails_if_reply_is_label_or_remote_id_changes(tmp_path):
    store, sid, user = seeded(tmp_path)
    provider = RepairProvider(); registry = Registry(); registry.register(provider)
    root = f'/openagent/sessions/{sid}/turns/project-test-001/repair'
    body = {'message':user.model_dump(mode='json'),'conversation_url':SLUG_ROUTE}
    async with AsyncClient(transport=ASGITransport(app=create_app(registry, store)),
                           base_url='http://test') as client:
        provider.answer = 'ChatGPT said:'
        r = await client.post(root, json=body)
        assert r.status_code == 409
        provider.answer = ANSWER
        async def wrong_chat(**kw):
            return ProviderOutput(content=ANSWER, upstream_conversation_id='different-chat-id')
        provider.reconcile_project_turn = wrong_chat
        r = await client.post(root, json=body)
        assert r.status_code == 409
        assert store.get(sid)['messages'][-1]['content'] == 'ChatGPT said:'
    store.close()


def test_repair_rejects_nonfinal_request_and_different_content(tmp_path):
    store, sid, user = seeded(tmp_path)
    with pytest.raises(SessionBusy):
        store.require_repairable_project_turn(sid, 'project-test-001',
            Message(role='user',content='different'),expected_project_id=PROJECT_ID)
    store.claim_turn(sid, 'second', Message(role='user',content='next'), expected_project_id=PROJECT_ID)
    with pytest.raises(SessionBusy):
        store.require_repairable_project_turn(sid, 'project-test-001', user,expected_project_id=PROJECT_ID)
    store.close()
