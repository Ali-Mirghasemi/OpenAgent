"""Manual Project recovery: strict journal identity and zero remote submission."""
import uuid

import pytest
from httpx import ASGITransport, AsyncClient

from openagent.api import create_app
from openagent.models import Message, ProviderOutput
from openagent.registry import Registry
from openagent.store import SessionBusy, SessionStore
from test_project_binding import PROJECT_ID
from test_project_routing import CID, ORIGIN, ProjectFixtureProvider

SLUG_ROUTE = f'{ORIGIN}/g/{PROJECT_ID}-openagent/c/{CID}'


class RecoveryProvider(ProjectFixtureProvider):
    def __init__(self):
        super().__init__(enabled=True)
        self.recovery_calls = []
        self.allow = True

    async def reconcile_project_turn(self, *, project_id, project_name, conversation_url, user_message):
        self.recovery_calls.append((project_id, project_name, conversation_url, user_message))
        if not self.allow:
            from openagent.providers.browser_ui import BrowserUIError
            raise BrowserUIError('reconcile_unverified', 'nothing verifiable')
        return ProviderOutput(content='OpenAgent Project test received.', upstream_conversation_id=CID)


@pytest.mark.asyncio
async def test_explicit_reconciliation_after_failed_turn_no_resubmission_and_restart(tmp_path):
    db = tmp_path / 'sessions.sqlite'
    store = SessionStore(db)
    provider = RecoveryProvider()
    provider.fail = True
    registry = Registry(); registry.register(provider)
    app = create_app(registry, store)
    sid = store.create('chatgpt-web/default')['id']
    store.bind_verified_project(sid, PROJECT_ID, 'OpenAgent')
    base = f'/openagent/sessions/{sid}/turns'
    message = {'role': 'user', 'content': 'Reply with exactly: OpenAgent Project test received.'}
    async with AsyncClient(transport=ASGITransport(app=app), base_url='http://test') as client:
        r = await client.post(base, json={'request_id': 'project-test-001', 'message': message})
        assert r.status_code == 502
        assert r.json()['error']['code'] == 'remote_outcome_unknown'
        assert r.json()['error']['request_status'] == 'unknown'
        assert len(provider.project_calls) == 1
        assert store.list_turns(sid)[0]['status'] == 'unknown'
        recovery = base + '/project-test-001/reconcile'
        body = {'message': message, 'conversation_url': SLUG_ROUTE}
        bad_message = await client.post(recovery, json={**body, 'message': {'role':'user','content':'different'}})
        assert bad_message.status_code == 409
        assert provider.recovery_calls == []
        provider.allow = False
        unverified = await client.post(recovery, json=body)
        assert unverified.status_code == 409
        assert store.list_turns(sid)[0]['status'] == 'unknown'
        provider.allow = True
        ok = await client.post(recovery, json=body)
        assert ok.status_code == 200 and ok.json()['reconciled'] is True
        assert ok.json()['output']['upstream_conversation_id'] == CID
        assert len(provider.project_calls) == 1  # no retransmission
        assert store.list_turns(sid)[0]['status'] == 'done'
        assert len(store.get(sid)['messages']) == 2
        repeated = await client.post(recovery, json=body)
        assert repeated.status_code == 409
        repeat_request = await client.post(base, json={'request_id':'project-test-001', 'message': message})
        assert repeat_request.status_code == 200 and repeat_request.json()['cached'] is True
    store.close()
    restored = SessionStore(db)
    assert restored.get(sid)['upstream']['conversation_id'] == CID
    assert restored.list_turns(sid)[0]['status'] == 'done'
    restored.close()


@pytest.mark.asyncio
async def test_cannot_reconcile_non_project_or_non_unknown(tmp_path):
    provider = RecoveryProvider(); reg = Registry(); reg.register(provider)
    store = SessionStore(tmp_path/'db.sqlite')
    app = create_app(reg, store)
    sid = store.create('chatgpt-web/default')['id']
    async with AsyncClient(transport=ASGITransport(app=app), base_url='http://test') as client:
        res = await client.post(f'/openagent/sessions/{sid}/turns/x/reconcile', json={
            'message': {'role':'user','content':'hello'}, 'conversation_url': SLUG_ROUTE,
        })
        assert res.status_code == 422
    assert not provider.recovery_calls
    store.close()


def test_unknown_reconciliation_requires_same_project_and_message(tmp_path):
    store = SessionStore(tmp_path/'db.sqlite')
    sid = store.create('chatgpt-web/default')['id']
    store.bind_verified_project(sid, PROJECT_ID, 'OpenAgent')
    message = Message(role='user', content='hello')
    store.claim_turn(sid, 'first', message, expected_project_id=PROJECT_ID)
    with pytest.raises(SessionBusy):
        store.require_unknown_project_turn(sid,'first',message,expected_project_id=PROJECT_ID)
    store.fail_turn(sid, 'first')
    for pid, body in [('g-p-'+('f'*32), message), (PROJECT_ID, Message(role='user', content='WRONG'))]:
        with pytest.raises(SessionBusy):
            store.require_unknown_project_turn(sid,'first',body,expected_project_id=pid)
    store.close()
