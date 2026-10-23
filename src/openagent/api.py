"""HTTP bridge (OpenAI-compatible subset + native session endpoints)."""
from __future__ import annotations

import asyncio
import json
from contextlib import asynccontextmanager
import secrets
import time
import uuid
from typing import Any

from fastapi import FastAPI, Header, HTTPException, Request
from fastapi.responses import JSONResponse, StreamingResponse

from . import __version__
from .models import CompletionRequest, Message, SessionCreate, SessionTurn, ProjectBindRequest, ProjectTurnReconcile
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


def create_app(registry: Registry, store: SessionStore, *, api_token: str | None = None,
               browser_manager: Any | None = None) -> FastAPI:
    @asynccontextmanager
    async def lifespan(_app: FastAPI):
        try:
            yield
        finally:
            if browser_manager is not None:
                await browser_manager.stop()

    app = FastAPI(title="OpenAgent Bridge", version=__version__, lifespan=lifespan)

    @app.middleware("http")
    async def authorize(request: Request, call_next):
        if api_token and request.url.path != "/health":
            credential = request.headers.get("authorization", "")
            if not credential.startswith("Bearer ") or not secrets.compare_digest(credential[7:], api_token):
                return error_response("invalid bearer token", 401, "authentication_error")
        return await call_next(request)

    @app.get("/health")
    async def health():
        return {"status": "ok", "service": "openagent-bridge", "version": __version__}

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

    @app.get("/openagent/providers/{provider_id}/diagnostics")
    async def provider_diagnostics(provider_id: str):
        provider = registry.get(provider_id)
        if provider is None or not callable(getattr(provider, "inspect", None)):
            raise HTTPException(404, "provider has no browser diagnostics")
        try:
            observation = await provider.inspect()
            return observation.as_dict()
        except Exception:
            return error_response("browser diagnostics unavailable", 503, "provider_error")

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
        except Exception as exc:
            # Only the adapter's finite, sanitized error codes are exposed.
            from .providers.browser_ui import BrowserUIError
            if isinstance(exc, BrowserUIError):
                status = 409 if exc.code == "remote_outcome_unknown" else 503
                return JSONResponse({"error": {
                    "message": str(exc), "type": "provider_error", "code": exc.code,
                }}, status_code=status)
            # Never leak page contents, browser paths, URLs or credentials.
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

    @app.post("/openagent/sessions/{session_id}/project")
    async def bind_project(session_id: str, body: ProjectBindRequest):
        """Bind only an empty session, after live read-only UI verification."""
        from .providers.chatgpt_web import CHATGPT_MODEL
        from .providers.chatgpt_projects import parse_project_url
        if parse_project_url(f"https://chatgpt.com/g/{body.project_id}/project") != body.project_id:
            return error_response("invalid Project ID", 422)
        try:
            session = store.get(session_id)
        except SessionNotFound:
            raise HTTPException(404, "unknown session")
        if session["model"] != CHATGPT_MODEL:
            return error_response("Project selection requires a ChatGPT web session", 422)
        # Check before opening the browser; storage checks again atomically.
        if session["messages"] or session["upstream"] or store.list_turns(session_id):
            return error_response("Project binding requires an unused session", 409, "session_conflict")
        if session["project"] and session["project"]["id"] != body.project_id:
            return error_response("session already bound to a different Project", 409, "session_conflict")
        provider = await registry.resolve(CHATGPT_MODEL)
        verify = getattr(provider, "verify_project", None)
        if not callable(verify):
            return error_response("Project verification unavailable for this adapter", 422)
        try:
            result = await verify(body.project_id)
        except asyncio.CancelledError:
            raise
        except Exception:
            return error_response("Project verification unavailable; nothing bound", 503, "provider_error")
        if result is None or result.state != "verified" or result.project_id != body.project_id:
            return error_response("Project destination unverified; nothing bound", 409, "session_conflict")
        try:
            return store.bind_verified_project(session_id, result.project_id, result.project_name)
        except SessionNotFound:
            raise HTTPException(404, "unknown session")
        except (SessionBusy, ValueError):
            return error_response("Project binding conflict; nothing changed", 409, "session_conflict")

    @app.get("/openagent/sessions/{session_id}/turns")
    async def get_session_turns(session_id: str):
        try:
            return {"session_id": session_id, "turns": store.list_turns(session_id)}
        except SessionNotFound:
            raise HTTPException(404, "unknown session")

    @app.post("/openagent/sessions/{session_id}/turns")
    async def run_turn(session_id: str, body: SessionTurn):
        try:
            session = store.get(session_id)
            provider = await registry.resolve(session["model"])
            # A separate operator flag is required for any Project submission.
            # Never route a bound session through the ordinary provider path.
            project = session["project"]
            if project is not None and not (
                getattr(provider, "supports_project_messages", False)
                and provider.supports_upstream_conversations
            ):
                return error_response("Project-scoped messaging not implemented or enabled; no request submitted", 422)
            # A bound upstream conversation must be continued by an adapter
            # that explicitly supports it, never silently restarted.
            if session["upstream"] and not provider.supports_upstream_conversations:
                return error_response("upstream continuation unsupported", 422)
            cached = store.claim_turn(
                session_id, body.request_id, body.message,
                expected_project_id=project["id"] if project else None,
            )
        except SessionNotFound:
            raise HTTPException(404, "unknown session")
        except SessionBusy as exc:
            return error_response(str(exc), 409, "session_conflict")
        if cached is not None:
            return {"cached": True, "output": cached, "session_id": session_id}
        try:
            # Re-read after acquiring the claim. Another request can finish
            # between the initial GET and the claim, updating the transcript.
            session = store.get(session_id)
            messages = [Message.model_validate(m) for m in session["messages"]] + [body.message]
            upstream_id = session["upstream"]["conversation_id"] if session["upstream"] else None
            # Use the transactionally claimed, server-verified project binding.
            if project is not None:
                output = await provider.complete_in_project_session(
                    session["model"], messages, project_id=project["id"],
                    project_name=project["name"], upstream_conversation_id=upstream_id,
                )
            else:
                output = await provider.complete_in_session(
                    session["model"], messages, upstream_conversation_id=upstream_id,
                )
            stored = store.complete_turn(
                session_id, body.request_id, body.message, output,
                supports_upstream_conversations=provider.supports_upstream_conversations,
            )
            return {"cached": False, "output": stored, "session_id": session_id}
        except asyncio.CancelledError:
            store.fail_turn(session_id, body.request_id)
            raise
        except Exception as exc:
            from .providers.browser_ui import BrowserUIError
            store.fail_turn(session_id, body.request_id)
            # Preserve a categorized reason without leaking DOM content or
            # credentials. Outcome stays unknown, so callers MUST NOT retry.
            code = exc.code if isinstance(exc, BrowserUIError) else "turn_outcome_unknown"
            return JSONResponse({"error": {
                "message": "turn outcome unverified; request will not be automatically retried",
                "type": "provider_error", "code": code, "request_status": "unknown",
            }}, status_code=502)

    @app.post("/openagent/sessions/{session_id}/turns/{request_id}/reconcile-check")
    async def check_project_reply(session_id: str, request_id: str, body: ProjectTurnReconcile):
        """Preflight a manually supplied Project reply without editing the journal.

        Finite stage codes only: never return original/remote message text,
        cookies, DOM HTML or a raw browser URL. No automatic retry or send.
        """
        from .providers.browser_ui import BrowserUIError
        try:
            session = store.get(session_id)
            project = session["project"]
            if project is None:
                return error_response("Only Project-bound unknown turns can be checked", 422)
            provider = await registry.resolve(session["model"])
            inspector = getattr(provider, "reconcile_project_turn", None)
            if not (getattr(provider, "supports_upstream_conversations", False) and
                    getattr(provider, "supports_project_messages", False) and callable(inspector)):
                return error_response("Project reply checking is not enabled", 422)
            if body.message.role != "user" or not isinstance(body.message.content, str):
                return error_response("Checking requires the original plain-text user message", 422)
            store.require_unknown_project_turn(
                session_id, request_id, body.message, expected_project_id=project["id"],
            )
            await inspector(project_id=project["id"], project_name=project["name"],
                            conversation_url=body.conversation_url, user_message=body.message.content)
            return {"verifiable": True, "submitted": False, "journal_changed": False,
                    "next_action": "reconcile"}
        except SessionNotFound:
            raise HTTPException(404, "unknown session")
        except SessionBusy:
            return error_response("Only an identical unknown turn can be checked", 409, "session_conflict")
        except BrowserUIError as exc:
            return {"verifiable": False, "reason": exc.code,
                    "submitted": False, "journal_changed": False}
        except (ValueError, TypeError):
            return {"verifiable": False, "reason": "reconcile_invalid_input",
                    "submitted": False, "journal_changed": False}
        except asyncio.CancelledError:
            raise
        except Exception:
            return error_response("Project reply check unavailable; nothing changed", 503, "provider_error")

    @app.post("/openagent/sessions/{session_id}/turns/{request_id}/reconcile-dom-check")
    async def check_project_reply_dom(session_id: str, request_id: str, body: ProjectTurnReconcile):
        """Read-only DOM marker counts for one still-unknown Project reply.

        Strict original-message journal verification prevents arbitrary remote
        URL probing through the API. No message text or raw DOM is returned.
        """
        from .providers.browser_ui import BrowserUIError
        try:
            session = store.get(session_id)
            project = session["project"]
            if project is None:
                return error_response("Only Project-bound unknown turns can be inspected", 422)
            provider = await registry.resolve(session["model"])
            inspector = getattr(provider, "inspect_project_conversation_dom", None)
            if not (getattr(provider, "supports_project_messages", False) and
                    getattr(provider, "supports_upstream_conversations", False) and callable(inspector)):
                return error_response("Project DOM inspection is not enabled", 422)
            if body.message.role != "user" or not isinstance(body.message.content, str):
                return error_response("Inspection requires the original plain-text user message", 422)
            store.require_unknown_project_turn(
                session_id, request_id, body.message, expected_project_id=project["id"])
            return await inspector(project_id=project["id"], conversation_url=body.conversation_url)
        except SessionNotFound:
            raise HTTPException(404, "unknown session")
        except SessionBusy:
            return error_response("Only an identical unknown turn can be inspected", 409, "session_conflict")
        except BrowserUIError as exc:
            return JSONResponse({"error": {"message": "Read-only DOM inspection unavailable",
                                           "type": "provider_error", "code": exc.code}}, status_code=409)
        except (ValueError, TypeError):
            return error_response("Project DOM inspection unavailable", 409, "reconcile_invalid_input")
        except asyncio.CancelledError:
            raise
        except Exception:
            return error_response("Project DOM inspection failed", 503, "provider_error")

    @app.post("/openagent/sessions/{session_id}/turns/{request_id}/repair-check")
    async def check_project_reply_repair(session_id: str, request_id: str, body: ProjectTurnReconcile):
        """Read-only verification of a *done* Project turn with a label-only reply.

        Unlike reconciliation this cannot act on an unknown or new turn.
        The original prompt must match the immutable journal payload.
        """
        from .providers.browser_ui import BrowserUIError
        try:
            session = store.get(session_id)
            project = session["project"]
            if project is None:
                return error_response("Only Project-bound turns can be repaired", 422)
            provider = await registry.resolve(session["model"])
            inspect = getattr(provider, "reconcile_project_turn", None)
            if not (getattr(provider, "supports_upstream_conversations", False) and
                    getattr(provider, "supports_project_messages", False) and callable(inspect)):
                return error_response("Project repair is not enabled", 422)
            if body.message.role != "user" or not isinstance(body.message.content, str):
                return error_response("Repair requires the original plain user text", 422)
            store.require_repairable_project_turn(
                session_id, request_id, body.message, expected_project_id=project["id"])
            output = await inspect(project_id=project["id"], project_name=project["name"],
                                   conversation_url=body.conversation_url,
                                   user_message=body.message.content)
            if output.upstream_conversation_id != session["upstream"]["conversation_id"]:
                return {"correctable": False, "reason": "repair_conversation_mismatch",
                        "submitted": False, "journal_changed": False}
            from .providers.reply_text import is_trustworthy_answer
            if (not isinstance(output.content, str) or
                    not is_trustworthy_answer(output.content) or output.tool_calls or
                    output.content.casefold().startswith(("chatgpt said:", "chatgpt says:",
                                                          "assistant said:", "assistant says:"))):
                return {"correctable": False, "reason": "reconcile_assistant_content_unverified",
                        "submitted": False, "journal_changed": False}
            return {"correctable": True, "submitted": False, "journal_changed": False,
                    "next_action": "repair"}
        except SessionNotFound:
            raise HTTPException(404, "unknown session")
        except SessionBusy:
            return error_response("Only the last identical completed label-only reply can be repaired",
                                  409, "session_conflict")
        except BrowserUIError as exc:
            return {"correctable": False, "reason": exc.code,
                    "submitted": False, "journal_changed": False}
        except asyncio.CancelledError:
            raise
        except Exception:
            return error_response("Project repair check unavailable", 503, "provider_error")

    @app.post("/openagent/sessions/{session_id}/turns/{request_id}/repair-dom-check")
    async def check_project_reply_shape(session_id: str, request_id: str, body: ProjectTurnReconcile):
        """Read-only, text-free answer shape inspection of a completed bad reply.

        Only the exact last label-only Project turn from the immutable journal
        may be inspected. No history or remote content is returned and this
        never sends a prompt or writes SQLite.
        """
        from .providers.browser_ui import BrowserUIError
        try:
            session = store.get(session_id)
            project = session["project"]
            if project is None:
                return error_response("Only Project-bound turns can be inspected", 422)
            provider = await registry.resolve(session["model"])
            inspector = getattr(provider, "inspect_project_repair_dom", None)
            if not (getattr(provider, "supports_upstream_conversations", False) and
                    getattr(provider, "supports_project_messages", False) and callable(inspector)):
                return error_response("Project answer inspection is not enabled", 422)
            if body.message.role != "user" or not isinstance(body.message.content, str):
                return error_response("Inspection requires the original plain user text", 422)
            store.require_repairable_project_turn(
                session_id, request_id, body.message, expected_project_id=project["id"])
            return await inspector(project_id=project["id"],
                                   conversation_url=body.conversation_url,
                                   user_message=body.message.content)
        except SessionNotFound:
            raise HTTPException(404, "unknown session")
        except SessionBusy:
            return error_response("Only the last identical label-only completed turn can be inspected",
                                  409, "session_conflict")
        except BrowserUIError as exc:
            return JSONResponse({"error": {"message": "Project answer inspection unavailable",
                                           "type": "provider_error", "code": exc.code}}, status_code=409)
        except asyncio.CancelledError:
            raise
        except Exception:
            return error_response("Project answer inspection failed", 503, "provider_error")

    @app.post("/openagent/sessions/{session_id}/turns/{request_id}/repair")
    async def repair_project_reply(session_id: str, request_id: str, body: ProjectTurnReconcile):
        """Opt-in one-time *correction* of a stored label-only assistant reply.

        A new remote verification and atomic SQLite compare-and-swap are
        mandatory. Never sends a prompt or overwrites a later completed turn.
        """
        from .providers.browser_ui import BrowserUIError
        try:
            session = store.get(session_id)
            project = session["project"]
            if project is None:
                return error_response("Only Project-bound turns can be repaired", 422)
            provider = await registry.resolve(session["model"])
            inspect = getattr(provider, "reconcile_project_turn", None)
            if not (getattr(provider, "supports_upstream_conversations", False) and
                    getattr(provider, "supports_project_messages", False) and callable(inspect)):
                return error_response("Project repair is not enabled", 422)
            if body.message.role != "user" or not isinstance(body.message.content, str):
                return error_response("Repair requires the original plain user text", 422)
            store.require_repairable_project_turn(
                session_id, request_id, body.message, expected_project_id=project["id"])
            output = await inspect(project_id=project["id"], project_name=project["name"],
                                   conversation_url=body.conversation_url,
                                   user_message=body.message.content)
            stored = store.repair_project_turn(
                session_id, request_id, body.message, output, expected_project_id=project["id"])
            return {"repaired": True, "output": stored, "session_id": session_id}
        except SessionNotFound:
            raise HTTPException(404, "unknown session")
        except SessionBusy:
            return error_response("Project reply cannot be repaired; journal unchanged", 409, "session_conflict")
        except BrowserUIError as exc:
            return JSONResponse({"error": {
                "message": "Project reply could not be verified; journal unchanged",
                "type": "provider_error", "code": exc.code,
            }}, status_code=409)
        except asyncio.CancelledError:
            raise
        except Exception:
            return error_response("Project reply repair unavailable; journal unchanged", 503, "provider_error")

    @app.post("/openagent/sessions/{session_id}/turns/{request_id}/reconcile")
    async def reconcile_project_turn(session_id: str, request_id: str, body: ProjectTurnReconcile):
        """Explicit read-only remote verification; NEVER submits a message."""
        from .providers.browser_ui import BrowserUIError
        try:
            session = store.get(session_id)
            project = session["project"]
            if project is None:
                return error_response("Only Project-bound unknown turns can be reconciled", 422)
            provider = await registry.resolve(session["model"])
            reconcile = getattr(provider, "reconcile_project_turn", None)
            if not (provider.supports_upstream_conversations and
                    getattr(provider, "supports_project_messages", False) and callable(reconcile)):
                return error_response("Project read-only reconciliation is not enabled", 422)
            if body.message.role != "user" or not isinstance(body.message.content, str):
                return error_response("Reconciliation requires the original plain-text user message", 422)
            store.require_unknown_project_turn(
                session_id, request_id, body.message, expected_project_id=project["id"],
            )
            output = await reconcile(
                project_id=project["id"], project_name=project["name"],
                conversation_url=body.conversation_url, user_message=body.message.content,
            )
            stored = store.complete_turn(
                session_id, request_id, body.message, output,
                supports_upstream_conversations=True, reconcile_unknown=True,
            )
            return {"reconciled": True, "output": stored, "session_id": session_id}
        except SessionNotFound:
            raise HTTPException(404, "unknown session")
        except SessionBusy:
            return error_response("Turn cannot be reconciled; journal state is unchanged", 409, "session_conflict")
        except BrowserUIError as exc:
            return JSONResponse({"error": {
                "message": "Project reply could not be verified; nothing changed",
                "type": "provider_error", "code": exc.code,
            }}, status_code=409)
        except (ValueError, TypeError):
            return error_response("Project reply could not be verified; nothing changed", 409, "provider_error")
        except asyncio.CancelledError:
            raise
        except Exception:
            return error_response("Project reconciliation failed; nothing changed", 503, "provider_error")

    return app
