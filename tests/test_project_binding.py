"""A verified Project can be bound to an unused session; never sent via ordinary chat."""
from __future__ import annotations

import sqlite3

import pytest
from httpx import ASGITransport, AsyncClient

from openagent.api import create_app
from openagent.models import Message, ProviderOutput
from openagent.providers.base import Provider
from openagent.providers.chatgpt_projects import ProjectDestinationReport
from openagent.providers.chatgpt_web import CHATGPT_MODEL
from openagent.registry import Registry
from openagent.store import SessionStore, SessionBusy
from openagent.capabilities import Capabilities

PROJECT_ID = "g-p-6ad516cbe000819184ae2c1ed16d4a22"
OTHER_ID = "g-p-aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"


class VerifyFixtureProvider(Provider):
    id = "chatgpt-web"

    def __init__(self):
        self.verification_calls = []
        self.mode = "ok"
        self.sent = 0

    async def models(self):
        return [CHATGPT_MODEL]

    async def capabilities(self, model):
        return Capabilities(provider=self.id, model=model, transport="mock", authentication="none")

    async def complete(self, model, messages, tools=None):
        self.sent += 1
        return ProviderOutput(content="sent")

    async def verify_project(self, project_id):
        self.verification_calls.append(project_id)
        if self.mode == "raise":
            raise RuntimeError("private browser page data must not escape")
        if self.mode == "none":
            return None
        return ProjectDestinationReport(
            state="verified" if self.mode == "ok" else "unverified",
            evidence="project_heading_matches_route" if self.mode == "ok" else "route_mismatch",
            project_id=project_id if self.mode != "wrong" else OTHER_ID,
            project_name="OpenAgent",
        )


def test_store_project_binding_migration_and_restart(tmp_path):
    dbpath = tmp_path / "existing.sqlite"
    con = sqlite3.connect(dbpath)
    con.executescript("""
    CREATE TABLE sessions (id TEXT PRIMARY KEY, model TEXT NOT NULL, created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL, messages TEXT NOT NULL, upstream_conversation_id TEXT);
    CREATE TABLE turns (session_id TEXT NOT NULL, request_id TEXT NOT NULL, status TEXT NOT NULL,
    response TEXT, payload_hash TEXT, PRIMARY KEY(session_id, request_id));
    INSERT INTO sessions VALUES('old','chatgpt-web/default','at','at','[]',NULL);
    """)
    con.commit(); con.close()
    store = SessionStore(dbpath)
    assert store.get("old")["project"] is None
    first = store.bind_verified_project("old", PROJECT_ID, "OpenAgent")
    assert first["project"] == {"id": PROJECT_ID, "name": "OpenAgent",
                               "verified_at": first["project"]["verified_at"], "source": "ui_verified"}
    store.close()
    store = SessionStore(dbpath)
    assert store.get("old")["project"] == first["project"]
    assert store.bind_verified_project("old", PROJECT_ID, "OpenAgent")["project"] == first["project"]
    with pytest.raises(SessionBusy, match="ordinary turn"):
        store.claim_turn("old", "racy", Message(role="user", content="cannot send"))
    assert store.list_turns("old") == []
    with pytest.raises(SessionBusy, match="different"):
        store.bind_verified_project("old", OTHER_ID, "Another")
    with pytest.raises(SessionBusy, match="different"):
        store.bind_verified_project("old", PROJECT_ID, "Spoofed")
    store.close()


def test_binding_rejects_prior_history_prior_turns_and_other_models(tmp_path):
    store = SessionStore(tmp_path / "sessions.db")
    a = store.create("mock/echo")["id"]
    with pytest.raises(SessionBusy, match="ChatGPT"):
        store.bind_verified_project(a, PROJECT_ID, "OpenAgent")
    b = store.create(CHATGPT_MODEL)["id"]
    store.db.execute("UPDATE sessions SET messages='[{\"role\": \"user\", \"content\": \"x\"}]' WHERE id=?", (b,))
    with pytest.raises(SessionBusy, match="empty"):
        store.bind_verified_project(b, PROJECT_ID, "OpenAgent")
    c = store.create(CHATGPT_MODEL)["id"]
    store.claim_turn(c, "pending", Message(role="user", content="sent?"))
    with pytest.raises(SessionBusy, match="empty"):
        store.bind_verified_project(c, PROJECT_ID, "OpenAgent")
    d = store.create(CHATGPT_MODEL)["id"]
    store.db.execute("UPDATE sessions SET upstream_conversation_id='existing' WHERE id=?", (d,))
    with pytest.raises(SessionBusy, match="empty"):
        store.bind_verified_project(d, PROJECT_ID, "OpenAgent")
    for bad in ("bad", "g-p-abc/../../", "g-p-not-valid", ""):
        with pytest.raises(ValueError):
            store.bind_verified_project(store.create(CHATGPT_MODEL)["id"], bad, "OpenAgent")
    store.close()


@pytest.mark.asyncio
async def test_api_binds_only_after_server_verified_destination(tmp_path):
    provider = VerifyFixtureProvider()
    registry = Registry(); registry.register(provider)
    store = SessionStore(tmp_path / "project.db")
    app = create_app(registry, store, api_token="local-test-secret")
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        headers = {"Authorization": "Bearer local-test-secret"}
        created = await client.post("/openagent/sessions", json={"model": CHATGPT_MODEL}, headers=headers)
        sid = created.json()["id"]
        url = f"/openagent/sessions/{sid}/project"
        assert (await client.post(url, json={"project_id": PROJECT_ID})).status_code == 401
        assert (await client.post(url, json={"project_id": "bad"}, headers=headers)).status_code == 422
        assert provider.verification_calls == []
        provider.mode = "none"
        assert (await client.post(url, json={"project_id": PROJECT_ID}, headers=headers)).status_code == 409
        assert store.get(sid)["project"] is None
        provider.mode = "wrong"
        assert (await client.post(url, json={"project_id": PROJECT_ID}, headers=headers)).status_code == 409
        assert store.get(sid)["project"] is None
        provider.mode = "raise"
        result = await client.post(url, json={"project_id": PROJECT_ID}, headers=headers)
        assert result.status_code == 503 and "private browser" not in result.text
        assert store.get(sid)["project"] is None
        provider.mode = "ok"
        result = await client.post(url, json={"project_id": PROJECT_ID}, headers=headers)
        assert result.status_code == 200
        assert result.json()["project"]["id"] == PROJECT_ID
        assert result.json()["project"]["name"] == "OpenAgent"
        assert result.json()["project"]["source"] == "ui_verified"
        assert (await client.post(url, json={"project_id": PROJECT_ID}, headers=headers)).status_code == 200
        assert (await client.post(url, json={"project_id": OTHER_ID}, headers=headers)).status_code == 409
        turn = await client.post(f"/openagent/sessions/{sid}/turns", json={
            "request_id": "not-sent", "message": {"role": "user", "content": "hello"}}, headers=headers)
        assert turn.status_code == 422
        assert "not implemented" in turn.text
        assert provider.sent == 0
        assert (await client.get(f"/openagent/sessions/{sid}/turns", headers=headers)).json()["turns"] == []
        assert (await client.get(f"/openagent/sessions/{sid}", headers=headers)).json()["project"]["id"] == PROJECT_ID
    store.close()


@pytest.mark.asyncio
async def test_api_rejects_non_chatgpt_and_used_sessions(tmp_path):
    from openagent.providers.mock import MockProvider
    provider = VerifyFixtureProvider()
    registry = Registry(); registry.register(provider); registry.register(MockProvider())
    store = SessionStore(tmp_path / "test.db")
    app = create_app(registry, store)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        sid = (await client.post("/openagent/sessions", json={"model":"mock/echo"})).json()["id"]
        assert (await client.post(f"/openagent/sessions/{sid}/project", json={"project_id": PROJECT_ID})).status_code == 422
        sid = (await client.post("/openagent/sessions", json={"model": CHATGPT_MODEL})).json()["id"]
        store.claim_turn(sid, "once", Message(role="user", content="hello"))
        assert (await client.post(f"/openagent/sessions/{sid}/project", json={"project_id": PROJECT_ID})).status_code == 409
        assert provider.verification_calls == []
    store.close()
