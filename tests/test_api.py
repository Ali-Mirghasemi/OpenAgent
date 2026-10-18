from __future__ import annotations
import json
import pytest
from httpx import ASGITransport, AsyncClient
from openagent.api import create_app
from openagent.models import ProviderOutput
from openagent.providers.base import Provider
from openagent.capabilities import Capabilities, Feature
from openagent.registry import Registry

@pytest.mark.asyncio
async def test_model_listing_and_capabilities(app):
    async with AsyncClient(transport=ASGITransport(app=app),base_url="http://test") as c:
        r = await c.get("/v1/models")
        assert r.status_code == 200
        assert r.json()["data"][0]["id"] == "mock/echo"
        cap = await c.get("/openagent/capabilities/mock/echo")
        assert cap.json()["native_tools"]["state"] == "unsupported"

@pytest.mark.asyncio
async def test_basic_completion_and_tool_rejection(app):
    async with AsyncClient(transport=ASGITransport(app=app),base_url="http://test") as c:
        response = await c.post("/v1/chat/completions",json={
            "model":"mock/echo", "messages":[{"role":"system","content":"test"},{"role":"user","content":"Hello"}]
        })
        assert response.status_code == 200
        assert response.json()["choices"][0]["message"]["content"] == "Echo: Hello"
        unsupported = await c.post("/v1/chat/completions",json={
            "model":"mock/echo", "messages":[{"role":"user","content":"Hello"}],
            "tools":[{"type":"function","function":{"name":"shell"}}]
        })
        assert unsupported.status_code == 422

@pytest.mark.asyncio
async def test_sse_contract(app):
    async with AsyncClient(transport=ASGITransport(app=app),base_url="http://test") as c:
        response = await c.post("/v1/chat/completions",json={
            "model":"mock/echo", "messages":[{"role":"user","content":"ping"}], "stream": True
        })
        assert response.status_code == 200
        text = response.text
        assert '"chat.completion.chunk"' in text
        assert "Echo: ping" in text
        assert text.endswith("data: [DONE]\n\n")

@pytest.mark.asyncio
async def test_session_replay_and_status(app):
    async with AsyncClient(transport=ASGITransport(app=app),base_url="http://test") as c:
        res=await c.post("/openagent/sessions",json={"model":"mock/echo"})
        sid=res.json()["id"]
        turn={"request_id":"req-1","message":{"role":"user","content":"one"}}
        first=await c.post(f"/openagent/sessions/{sid}/turns",json=turn)
        assert first.status_code==200
        assert first.json()["cached"] is False
        second=await c.post(f"/openagent/sessions/{sid}/turns",json=turn)
        assert second.json()["cached"] is True
        saved=await c.get(f"/openagent/sessions/{sid}")
        assert [m["role"] for m in saved.json()["messages"]] == ["user", "assistant"]
        assert saved.json()["messages"][0]["content"] == "one"

@pytest.mark.asyncio
async def test_request_history_preserved(store):
    class CaptureProvider(Provider):
        id="capture"
        MODEL="capture/inspect"
        async def models(self):return [self.MODEL]
        async def capabilities(self, model):
            return Capabilities(provider="capture",model=model,transport="mock",authentication="none",
                                native_tools=Feature(state="supported",evidence="tested"))
        async def complete(self,model,messages,tools=None):
            self.saved=messages
            return ProviderOutput(content="recorded")
    p=CaptureProvider()
    registry=Registry();registry.register(p)
    a=create_app(registry,store)
    payload={"model":"capture/inspect","messages":[
        {"role":"system","content":"system instructions"},
        {"role":"user","content":"run"},
        {"role":"assistant","content":None,"tool_calls":[{"id":"t-1","type":"function","function":{"name":"cmd","arguments":"{}"}}]},
        {"role":"tool","tool_call_id":"t-1","content":"exit 0"},
        {"role":"developer","content":"remain concise"},
        {"role":"user","content":"continue"}
    ]}
    async with AsyncClient(transport=ASGITransport(app=a),base_url="http://test") as c:
        response=await c.post("/v1/chat/completions",json=payload)
        assert response.status_code==200
    assert [m.role for m in p.saved] == [m["role"] for m in payload["messages"]]
    assert p.saved[2].tool_calls[0]["id"] == "t-1"
    assert p.saved[3].tool_call_id == "t-1"

@pytest.mark.asyncio
async def test_token_auth(registry, store):
    app=create_app(registry,store,api_token="abc123")
    async with AsyncClient(transport=ASGITransport(app=app),base_url="http://test") as c:
        assert (await c.get("/v1/models")).status_code==401
        assert (await c.get("/v1/models",headers={"Authorization":"Bearer abc123"})).status_code==200
        assert (await c.get("/health")).status_code==200
