"""Provider-issued upstream conversation identity, migration, and fail-closed behavior."""
from __future__ import annotations

import asyncio
import sqlite3

import pytest
from httpx import ASGITransport, AsyncClient

from openagent.api import create_app
from openagent.capabilities import Capabilities, Feature
from openagent.models import Message, ProviderOutput
from openagent.providers.base import Provider
from openagent.registry import Registry
from openagent.store import SessionBusy, SessionStore


class ThreadFixtureProvider(Provider):
    """Offline, deterministic upstream thread simulator; no browser or account."""

    id = "fixture"
    supports_upstream_conversations = True
    MODEL = "fixture/thread"

    def __init__(self):
        self.calls = []
        self.changed_ref = False

    async def models(self):
        return [self.MODEL]

    async def capabilities(self, model):
        return Capabilities(provider=self.id, model=model, transport="mock", authentication="none",
                            persistent_chat=Feature(state="supported", evidence="tested"))

    async def complete(self, model, messages, tools=None):
        raise AssertionError("Session path must use complete_in_session, not one-shot complete")

    async def complete_in_session(self, model, messages, *, upstream_conversation_id):
        assert model == self.MODEL
        self.calls.append((upstream_conversation_id, [m.content for m in messages]))
        return ProviderOutput(
            content=f"step-{len(self.calls)}",
            upstream_conversation_id="different-thread" if self.changed_ref else "upstream-1234",
        )


def test_migrate_existing_database_and_keep_transcript(tmp_path):
    path = tmp_path / "previous.sqlite"
    db = sqlite3.connect(path)
    db.executescript("""
      CREATE TABLE sessions(id TEXT PRIMARY KEY, model TEXT NOT NULL,
        created_at TEXT NOT NULL, updated_at TEXT NOT NULL, messages TEXT NOT NULL);
      CREATE TABLE turns(session_id TEXT NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
        request_id TEXT NOT NULL, status TEXT NOT NULL, response TEXT, payload_hash TEXT,
        PRIMARY KEY(session_id,request_id));
      INSERT INTO sessions VALUES ('legacy','mock/echo','old','old','[{"role":"user","content":"saved"}]');
    """)
    db.close()
    store = SessionStore(path)
    assert store.get("legacy")["upstream"] is None
    assert store.get("legacy")["messages"] == [{"role": "user", "content": "saved"}]
    store.close()
    store = SessionStore(path)  # migration must be idempotent
    assert store.get("legacy")["upstream"] is None
    store.close()


@pytest.mark.parametrize("bad", ["", "chat/abc", "?token=x", "id with space", "a"*129])
def test_upstream_ids_must_be_safe_opaque_identifiers(bad):
    from pydantic import ValidationError
    with pytest.raises(ValidationError):
        ProviderOutput(content="ok", upstream_conversation_id=bad)


def test_prevent_unauthorized_binding_or_thread_switch(tmp_path):
    db = SessionStore(tmp_path / "store.sqlite")
    sid = db.create("mock/echo")["id"]
    msg = Message(role="user", content="ping")
    db.claim_turn(sid, "1", msg)
    with pytest.raises(SessionBusy, match="not permitted"):
        db.complete_turn(sid, "1", msg,
                         ProviderOutput(content="ok", upstream_conversation_id="thread1"))
    assert db.get(sid)["messages"] == []
    assert db.get(sid)["upstream"] is None
    db.complete_turn(sid, "1", msg, ProviderOutput(content="ok"))
    db.claim_turn(sid, "2", msg)
    db.complete_turn(sid, "2", msg, ProviderOutput(content="ok", upstream_conversation_id="thread1"),
                     supports_upstream_conversations=True)
    db.claim_turn(sid, "3", msg)
    with pytest.raises(SessionBusy, match="changed unexpectedly"):
        db.complete_turn(sid, "3", msg,
                         ProviderOutput(content="wrong", upstream_conversation_id="thread2"),
                         supports_upstream_conversations=True)
    assert db.get(sid)["upstream"]["conversation_id"] == "thread1"
    assert len(db.get(sid)["messages"]) == 4
    db.fail_turn(sid, "3")
    assert db.list_turns(sid)[-1] == {"request_id": "3", "status": "unknown", "has_response": False}
    db.close()


def test_do_not_append_without_reserved_pending_turn(tmp_path):
    db = SessionStore(tmp_path / "store.sqlite")
    sid = db.create("mock/echo")["id"]
    with pytest.raises(SessionBusy, match="not pending"):
        db.complete_turn(sid, "not-claimed", Message(role="user", content="a"),
                         ProviderOutput(content="b"))
    assert db.get(sid)["messages"] == []
    db.close()


@pytest.mark.asyncio
async def test_upstream_binding_restores_across_process_restarts(tmp_path):
    path = tmp_path / "sessions.sqlite"
    provider = ThreadFixtureProvider()
    registry = Registry()
    registry.register(provider)
    db = SessionStore(path)
    app = create_app(registry, db)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        created = await client.post("/openagent/sessions", json={"model": provider.MODEL})
        sid = created.json()["id"]
        first = await client.post(f"/openagent/sessions/{sid}/turns", json={
            "request_id": "first", "message": {"role": "user", "content": "initial"},
        })
        assert first.status_code == 200
        assert first.json()["output"]["upstream_conversation_id"] == "upstream-1234"
        assert provider.calls == [(None, ["initial"])]
        assert (await client.get(f"/openagent/sessions/{sid}")).json()["upstream"] == {
            "conversation_id": "upstream-1234", "source": "provider_reported",
        }
    db.close()
    db = SessionStore(path)  # Fresh process, same durable journal.
    app = create_app(registry, db)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        second = await client.post(f"/openagent/sessions/{sid}/turns", json={
            "request_id": "second", "message": {"role": "user", "content": "next"},
        })
        assert second.status_code == 200
        assert provider.calls[-1] == ("upstream-1234", ["initial", "step-1", "next"])
        cached = await client.post(f"/openagent/sessions/{sid}/turns", json={
            "request_id": "second", "message": {"role": "user", "content": "next"},
        })
        assert cached.status_code == 200 and cached.json()["cached"]
        assert len(provider.calls) == 2  # no duplicate upstream request
        ledger = await client.get(f"/openagent/sessions/{sid}/turns")
        assert ledger.json()["turns"] == [
            {"request_id": "first", "status": "done", "has_response": True},
            {"request_id": "second", "status": "done", "has_response": True},
        ]
    db.close()


@pytest.mark.asyncio
async def test_upstream_mismatch_becomes_unknown_and_blocks_retry(tmp_path):
    provider = ThreadFixtureProvider()
    registry = Registry(); registry.register(provider)
    db = SessionStore(tmp_path / "store.sqlite")
    app = create_app(registry, db)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        sid = (await client.post("/openagent/sessions", json={"model": provider.MODEL})).json()["id"]
        path = f"/openagent/sessions/{sid}/turns"
        request = lambda key: {"request_id": key, "message": {"role": "user", "content": key}}
        assert (await client.post(path, json=request("one"))).status_code == 200
        provider.changed_ref = True
        assert (await client.post(path, json=request("two"))).status_code == 502
        assert (await client.post(path, json=request("three"))).status_code == 409
        ledger = (await client.get(f"/openagent/sessions/{sid}/turns")).json()["turns"]
        assert [r["status"] for r in ledger] == ["done", "unknown"]
    db.close()


@pytest.mark.asyncio
async def test_bound_provider_cannot_fall_back_to_one_shot(tmp_path):
    from openagent.providers.mock import MockProvider
    from openagent.store import SessionStore
    db = SessionStore(tmp_path / "db.sqlite")
    sid = db.create("mock/echo")["id"]
    # Represent a legacy/externally restored binding; do not execute such work.
    db.db.execute("UPDATE sessions SET upstream_conversation_id='opaque-123' WHERE id=?", (sid,))
    registry=Registry(); registry.register(MockProvider())
    async with AsyncClient(transport=ASGITransport(app=create_app(registry, db)), base_url="http://test") as client:
        response=await client.post(f"/openagent/sessions/{sid}/turns", json={
            "request_id":"n", "message":{"role":"user","content":"resume"}})
        assert response.status_code == 422
        assert db.list_turns(sid) == []
    db.close()


@pytest.mark.asyncio
async def test_cancelled_provider_turn_is_unknown(tmp_path):
    class CancelProvider(ThreadFixtureProvider):
        async def complete_in_session(self, model, messages, *, upstream_conversation_id):
            raise asyncio.CancelledError()
    p = CancelProvider(); registry=Registry(); registry.register(p)
    db=SessionStore(tmp_path/"db.sqlite")
    app=create_app(registry,db)
    sid=db.create(p.MODEL)["id"]
    # Invoke the route itself: Starlette BaseHTTPMiddleware transforms a
    # synthetic CancelledError into a transport-level 'No response returned'.
    # Real client disconnects are handled by ASGI cancellation, not an HTTP body.
    from openagent.models import SessionTurn
    route = next(r for r in app.routes if getattr(r, "path", None) ==
                 "/openagent/sessions/{session_id}/turns" and "POST" in r.methods)
    with pytest.raises(asyncio.CancelledError):
        await route.endpoint(sid, SessionTurn(
            request_id="cancel", message=Message(role="user", content="hello"),
        ))
    assert db.list_turns(sid)[0]["status"] == "unknown"
    db.close()
