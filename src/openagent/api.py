"""HTTP bridge (OpenAI-compatible subset + native session endpoints)."""
from __future__ import annotations

import json
import secrets
import time
import uuid
from typing import Any

from fastapi import FastAPI, Header, HTTPException, Request
from fastapi.responses import JSONResponse, StreamingResponse

from .models import CompletionRequest, Message, SessionCreate, SessionTurn
from .registry import Registry
from .store import SessionBusy, SessionNotFound, SessionStore


def error_response(message: str, status: int, kind: str = "invalid_request_error") -> JSONResponse:
    return JSONResponse({"error": {"message": message, "type": kind}}, status_code=status)


def format_completion(model: str, output: Any, request_id: str) -> dict[str, Any]:
    message: dict[str, Any] = {"role": "assistant", "content": output.content}
    if output.tool_calls:
        message["tool_calls"] = output.tool_calls
    return {
        "id": request_id,
        "object": "chat.completion",
        "created": int(time.time()),
        "model": model,
        "choices": [{"index": 0, "message": message, "finish_reason": output.finish_reason}],
        "usage": None,  # Cannot infer provider token usage.
    }


def sse_completion(model: str, output: Any, request_id: str):
    created = int(time.time())
    def chunk(delta: dict[str, Any], finish: str | None = None) -> str:
        data = {
            "id": request_id, "object": "chat.completion.chunk", "created": created,
            "model": model, "choices": [{"index": 0, "delta": delta, "finish_reason": finish}],
        }
        return f"data: {json.dumps(data, separators=(',', ':'))}\n\n"
    yield chunk({"role": "assistant"})
    if output.content is not None:
        yield chunk({"content": output.content})
    if output.tool_calls:
        yield chunk({"tool_calls": output.tool_calls})
    yield chunk({}, output.finish_reason)
    yield "data: [DONE]\n\n"


def create_app(registry: Registry, store: SessionStore, *, api_token: str | None = None) -> FastAPI:
    app = FastAPI(title="OpenAgent Bridge", version="0.2.0")

    @app.middleware("http")
    async def authorize(request: Request, call_next):
        if api_token and request.url.path != "/health":
            credential = request.headers.get("authorization", "")
            if not credential.startswith("Bearer ") or not secrets.compare_digest(credential[7:], api_token):
                return error_response("invalid bearer token", 401, "authentication_error")
        return await call_next(request)

    @app.get("/health")
    async def health():
        return {"status": "ok", "service": "openagent-bridge", "version": "0.2.0"}

    @app.get("/v1/models")
    async def models():
        return {"object": "list", "data": [
            {"id": model, "object": "model", "created": 0, "owned_by": model.split("/")[0]}
            for model in await registry.all_models()
        ]}

    @app.get("/openagent/capabilities/{provider_id}/{model_name}")
    async def capabilities(provider_id: str, model_name: str):
        model = f"{provider_id}/{model_name}"
        try:
            provider = await registry.resolve(model)
        except KeyError:
            raise HTTPException(404, "unknown model")
        return (await provider.capabilities(model)).model_dump(mode="json")

    @app.post("/v1/chat/completions")
    async def completions(body: CompletionRequest):
        try:
            provider = await registry.resolve(body.model)
        except KeyError:
            return error_response("unknown model", 404)
        caps = await provider.capabilities(body.model)
        if body.tools and caps.native_tools.state != "supported":
            return error_response("native tool calling unsupported for this adapter", 422)
        if body.stream and caps.streaming.state != "supported":
            return error_response("streaming unsupported for this adapter", 422)
        try:
            output = await provider.complete(body.model, body.messages, tools=body.tools)
        except Exception:
            # Do not leak web credentials, URLs, or sensitive provider internals.
            return error_response("provider execution failed", 502, "provider_error")
        request_id = "chatcmpl-" + uuid.uuid4().hex
        if body.stream:
            return StreamingResponse(sse_completion(body.model, output, request_id), media_type="text/event-stream")
        return format_completion(body.model, output, request_id)

    @app.post("/openagent/sessions", status_code=201)
    async def create_session(body: SessionCreate):
        try:
            await registry.resolve(body.model)
        except KeyError:
            raise HTTPException(404, "unknown model")
        return store.create(body.model)

    @app.get("/openagent/sessions/{session_id}")
    async def get_session(session_id: str):
        try:
            return store.get(session_id)
        except SessionNotFound:
            raise HTTPException(404, "unknown session")

    @app.post("/openagent/sessions/{session_id}/turns")
    async def run_turn(session_id: str, body: SessionTurn):
        try:
            session = store.get(session_id)
            provider = await registry.resolve(session["model"])
            cached = store.claim_turn(session_id, body.request_id, body.message)
        except SessionNotFound:
            raise HTTPException(404, "unknown session")
        except SessionBusy as exc:
            return error_response(str(exc), 409, "session_conflict")
        if cached is not None:
            return {"cached": True, "output": cached, "session_id": session_id}
        try:
            messages = [Message.model_validate(m) for m in session["messages"]] + [body.message]
            output = await provider.complete(session["model"], messages)
            stored = store.complete_turn(session_id, body.request_id, body.message, output)
            return {"cached": False, "output": stored, "session_id": session_id}
        except Exception:
            store.fail_turn(session_id, body.request_id)
            return error_response("turn failed; request will not be automatically retried", 502, "provider_error")

    return app
