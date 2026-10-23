"""Small SQLite session journal with explicit pending/unknown outcome handling."""
from __future__ import annotations

import json
import hashlib
import sqlite3
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .models import Message, ProviderOutput, valid_upstream_id
from .providers.chatgpt_projects import parse_project_url
from .providers.reply_text import is_role_label_only, is_trustworthy_answer


class SessionNotFound(KeyError):
    pass


class SessionBusy(RuntimeError):
    pass


def utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()


class SessionStore:
    def __init__(self, path: Path | str) -> None:
        self.path = str(path)
        if self.path != ":memory:":
            Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(self.path, check_same_thread=False, isolation_level=None)
        self.db.row_factory = sqlite3.Row
        self.db.execute("PRAGMA foreign_keys = ON")
        self.db.execute("PRAGMA busy_timeout = 5000")
        self.db.executescript("""
            CREATE TABLE IF NOT EXISTS sessions (
                id TEXT PRIMARY KEY,
                model TEXT NOT NULL,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                messages TEXT NOT NULL,
                upstream_conversation_id TEXT,
                project_id TEXT,
                project_name TEXT,
                project_verified_at TEXT
            );
            CREATE TABLE IF NOT EXISTS turns (
                session_id TEXT NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
                request_id TEXT NOT NULL,
                status TEXT NOT NULL,
                response TEXT,
                payload_hash TEXT,
                PRIMARY KEY (session_id, request_id)
            );
            CREATE TABLE IF NOT EXISTS turn_corrections (
                session_id TEXT NOT NULL,
                request_id TEXT NOT NULL,
                previous_content_sha256 TEXT NOT NULL,
                corrected_at TEXT NOT NULL,
                PRIMARY KEY (session_id, request_id),
                FOREIGN KEY (session_id, request_id) REFERENCES turns(session_id, request_id)
            );
        """)
        # Backward-compatible migration for existing v0.4.7 databases.
        fields = {row["name"] for row in self.db.execute("PRAGMA table_info(sessions)")}
        if "upstream_conversation_id" not in fields:
            self.db.execute("ALTER TABLE sessions ADD COLUMN upstream_conversation_id TEXT")
        for field in ("project_id", "project_name", "project_verified_at"):
            if field not in fields:
                self.db.execute(f"ALTER TABLE sessions ADD COLUMN {field} TEXT")

    def create(self, model: str) -> dict[str, Any]:
        sid, now = uuid.uuid4().hex, utcnow()
        self.db.execute(
            "INSERT INTO sessions(id,model,created_at,updated_at,messages) VALUES (?,?,?,?,?)",
            (sid, model, now, now, "[]"),
        )
        return self.get(sid)

    def get(self, session_id: str) -> dict[str, Any]:
        row = self.db.execute("SELECT * FROM sessions WHERE id=?", (session_id,)).fetchone()
        if row is None:
            raise SessionNotFound(session_id)
        return {
            "id": row["id"], "model": row["model"],
            "created_at": row["created_at"], "updated_at": row["updated_at"],
            "messages": json.loads(row["messages"]),
            "upstream": (
                {"conversation_id": row["upstream_conversation_id"], "source": "provider_reported"}
                if row["upstream_conversation_id"] else None
            ),
            "project": (
                {"id": row["project_id"], "name": row["project_name"],
                 "verified_at": row["project_verified_at"], "source": "ui_verified"}
                if row["project_id"] else None
            ),
        }

    def bind_verified_project(self, session_id: str, project_id: str, name: str,
                              *, expected_model: str = "chatgpt-web/default") -> dict[str, Any]:
        """Immutable, verified Project binding; only an empty ChatGPT session.

        UI verification must happen before calling this method. Transactionally
        refuse races with turns/history/upstream identities. An existing binding
        can only be repeated with exactly the same project and name.
        """
        canonical = f"https://chatgpt.com/g/{project_id}/project"
        if parse_project_url(canonical) != project_id:
            raise ValueError("invalid Project identifier")
        if not isinstance(name, str) or not name.strip() or name.strip() == project_id or len(name) > 160:
            raise ValueError("unverified Project name")
        with self.db:
            self.db.execute("BEGIN IMMEDIATE")
            row = self.db.execute(
                "SELECT model,messages,upstream_conversation_id,project_id,project_name FROM sessions WHERE id=?",
                (session_id,),
            ).fetchone()
            if row is None:
                raise SessionNotFound(session_id)
            if row["model"] != expected_model:
                raise SessionBusy("Project binding requires a ChatGPT web session")
            if row["project_id"] is not None:
                if row["project_id"] != project_id or row["project_name"] != name:
                    raise SessionBusy("session is already bound to a different Project")
            elif (json.loads(row["messages"]) or row["upstream_conversation_id"] is not None or
                  self.db.execute("SELECT 1 FROM turns WHERE session_id=? LIMIT 1", (session_id,)).fetchone()):
                raise SessionBusy("Project binding requires an empty session without prior turns")
            else:
                self.db.execute(
                    "UPDATE sessions SET project_id=?, project_name=?,project_verified_at=?,updated_at=? WHERE id=?",
                    (project_id, name, utcnow(), utcnow(), session_id),
                )
        return self.get(session_id)

    def list_turns(self, session_id: str) -> list[dict[str, Any]]:
        """Read-only outcome ledger; never automatically replays unknown work."""
        self.get(session_id)  # enforce session existence
        rows = self.db.execute(
            "SELECT request_id,status,response FROM turns WHERE session_id=? ORDER BY rowid",
            (session_id,),
        ).fetchall()
        return [{"request_id": row["request_id"], "status": row["status"],
                 "has_response": row["response"] is not None} for row in rows]

    def claim_turn(self, session_id: str, request_id: str, message: Message, *,
                   expected_project_id: str | None = None) -> dict[str, Any] | None:
        """Return a cached completed response or reserve new work exactly once.

        Pending and unknown-outcome turns are NOT retried automatically: a transport can
        succeed remotely just before local recording fails.
        """
        payload_hash = hashlib.sha256(json.dumps(message.model_dump(mode="json", exclude_none=True), sort_keys=True).encode()).hexdigest()
        with self.db:
            self.db.execute("BEGIN IMMEDIATE")
            session_row = self.db.execute(
                "SELECT project_id FROM sessions WHERE id=?", (session_id,)
            ).fetchone()
            if not session_row:
                raise SessionNotFound(session_id)
            # Atomic route guard against API/UI races: a normal request must
            # remain unbound; a Project request must match the exact binding.
            if session_row["project_id"] != expected_project_id:
                raise SessionBusy("session Project binding changed or ordinary turn was attempted")
            row = self.db.execute(
                "SELECT status,response,payload_hash FROM turns WHERE session_id=? AND request_id=?",
                (session_id, request_id),
            ).fetchone()
            if row:
                if row["payload_hash"] != payload_hash:
                    raise SessionBusy(f"request {request_id} was already used with different content")
                if row["status"] == "done":
                    return json.loads(row["response"])
                raise SessionBusy(f"request {request_id} status={row['status']}; manual reconciliation required")
            busy = self.db.execute(
                "SELECT request_id FROM turns WHERE session_id=? AND status IN ('pending','unknown')",
                (session_id,),
            ).fetchone()
            if busy:
                raise SessionBusy(f"session has unresolved request {busy['request_id']}")
            self.db.execute(
                "INSERT INTO turns(session_id,request_id,status,payload_hash) VALUES (?,?,'pending',?)",
                (session_id, request_id, payload_hash),
            )
        return None

    def require_unknown_project_turn(self, session_id: str, request_id: str, user: Message,
                                     *, expected_project_id: str) -> None:
        """Preflight a manual recovery without changing the session journal."""
        payload_hash = hashlib.sha256(json.dumps(
            user.model_dump(mode="json", exclude_none=True), sort_keys=True,
        ).encode()).hexdigest()
        session = self.db.execute(
            "SELECT project_id FROM sessions WHERE id=?", (session_id,),
        ).fetchone()
        if session is None:
            raise SessionNotFound(session_id)
        if session["project_id"] != expected_project_id:
            raise SessionBusy("Project binding mismatch")
        turn = self.db.execute(
            "SELECT status,payload_hash FROM turns WHERE session_id=? AND request_id=?",
            (session_id, request_id),
        ).fetchone()
        if turn is None or turn["status"] != "unknown" or turn["payload_hash"] != payload_hash:
            raise SessionBusy("Only an unknown turn with identical original message can be reconciled")

    def complete_turn(self, session_id: str, request_id: str, user: Message, output: ProviderOutput,
                      *, supports_upstream_conversations: bool = False,
                      reconcile_unknown: bool = False) -> dict[str, Any]:
        """Atomically commit both transcript messages and a replayable reply."""
        payload = output.model_dump(mode="json")
        with self.db:
            self.db.execute("BEGIN IMMEDIATE")
            row = self.db.execute(
                "SELECT messages,upstream_conversation_id FROM sessions WHERE id=?", (session_id,)
            ).fetchone()
            if row is None:
                raise SessionNotFound(session_id)
            turn = self.db.execute(
                "SELECT status FROM turns WHERE session_id=? AND request_id=?", (session_id, request_id)
            ).fetchone()
            status_required = "unknown" if reconcile_unknown else "pending"
            if turn is None or turn["status"] != status_required:
                raise SessionBusy(
                    "turn is not unknown; refusing to reconcile transcript"
                    if reconcile_unknown else "turn is not pending; refusing to append transcript"
                )
            previous = row["upstream_conversation_id"]
            reported = output.upstream_conversation_id
            if reported is not None:
                if not supports_upstream_conversations or not valid_upstream_id(reported):
                    raise SessionBusy("provider is not permitted to bind an upstream conversation")
                if previous is not None and previous != reported:
                    raise SessionBusy("upstream conversation changed unexpectedly; manual reconciliation required")
            entries = json.loads(row["messages"])
            entries.append(user.model_dump(mode="json", exclude_none=True))
            entries.append(Message(role="assistant", content=output.content, tool_calls=output.tool_calls).model_dump(mode="json", exclude_none=True))
            self.db.execute(
                "UPDATE sessions SET messages=?,updated_at=?,upstream_conversation_id=COALESCE(upstream_conversation_id, ?) WHERE id=?",
                (json.dumps(entries), utcnow(), reported, session_id),
            )
            self.db.execute(
                "UPDATE turns SET status='done',response=? WHERE session_id=? AND request_id=? AND status=?",
                (json.dumps(payload), session_id, request_id, status_required),
            )
        return payload

    def require_repairable_project_turn(self, session_id: str, request_id: str,
                                        user: Message, *, expected_project_id: str) -> None:
        """A completed but label-only final answer can be repaired exactly once.

        Check without changing the journal. This check is repeated under
        BEGIN IMMEDIATE when committing to reject competing writes.
        """
        digest = hashlib.sha256(json.dumps(user.model_dump(
            mode="json", exclude_none=True), sort_keys=True).encode()).hexdigest()
        session = self.db.execute("SELECT * FROM sessions WHERE id=?", (session_id,)).fetchone()
        if session is None:
            raise SessionNotFound(session_id)
        if session["model"] != "chatgpt-web/default" or session["project_id"] != expected_project_id:
            raise SessionBusy("Project repair requires the original bound model and Project")
        if not session["upstream_conversation_id"]:
            raise SessionBusy("Project repair requires a recorded upstream conversation")
        last = self.db.execute(
            "SELECT request_id,status,response,payload_hash FROM turns WHERE session_id=? ORDER BY rowid DESC LIMIT 1",
            (session_id,),
        ).fetchone()
        if (last is None or last["request_id"] != request_id or last["status"] != "done"
            or last["payload_hash"] != digest or not last["response"]):
            raise SessionBusy("Only the most recent completed identical Project turn can be repaired")
        old = json.loads(last["response"])
        entries = json.loads(session["messages"])
        if (not is_role_label_only(old.get("content") or "") or
            old.get("upstream_conversation_id") != session["upstream_conversation_id"] or
            len(entries) < 2 or
            entries[-2] != user.model_dump(mode="json", exclude_none=True) or
            entries[-1].get("role") != "assistant" or
            entries[-1].get("content") != old["content"]):
            raise SessionBusy("Saved answer is not the expected label-only last assistant turn")
        if self.db.execute(
            "SELECT 1 FROM turn_corrections WHERE session_id=? AND request_id=?",
            (session_id, request_id),
        ).fetchone():
            raise SessionBusy("Project turn was already corrected")

    def repair_project_turn(self, session_id: str, request_id: str, user: Message,
                            output: ProviderOutput, *, expected_project_id: str) -> dict[str, Any]:
        """One-time CAS update of a label-only reply, never a new remote send.

        The remote check happens before entering this transaction. We recheck
        session/turn identity atomically, update transcript and cache together,
        and retain a SHA-256 audit fingerprint of the old label-only content.
        """
        if (not is_trustworthy_answer(output.content) or
            output.content.casefold().startswith(("chatgpt said:", "chatgpt says:",
                                                   "assistant said:", "assistant says:")) or
            output.tool_calls):
            raise SessionBusy("The new answer is not verified user-visible content")
        with self.db:
            self.db.execute("BEGIN IMMEDIATE")
            self.require_repairable_project_turn(session_id, request_id, user,
                                                 expected_project_id=expected_project_id)
            row = self.db.execute("SELECT messages,upstream_conversation_id FROM sessions WHERE id=?",
                                  (session_id,)).fetchone()
            if output.upstream_conversation_id != row["upstream_conversation_id"]:
                raise SessionBusy("Conversation identity changed during Project repair")
            old = self.db.execute(
                "SELECT response FROM turns WHERE session_id=? AND request_id=?", (session_id, request_id)
            ).fetchone()
            previous = json.loads(old["response"])
            entries = json.loads(row["messages"])
            entries[-1]["content"] = output.content
            now = utcnow()
            self.db.execute("UPDATE sessions SET messages=?,updated_at=? WHERE id=?",
                            (json.dumps(entries), now, session_id))
            payload = output.model_dump(mode="json")
            self.db.execute("UPDATE turns SET response=? WHERE session_id=? AND request_id=?",
                            (json.dumps(payload), session_id, request_id))
            self.db.execute("INSERT INTO turn_corrections VALUES (?,?,?,?)",
                (session_id, request_id,
                 hashlib.sha256(previous["content"].encode()).hexdigest(), now))
        return payload

    def fail_turn(self, session_id: str, request_id: str) -> None:
        self.db.execute(
            "UPDATE turns SET status='unknown' WHERE session_id=? AND request_id=? AND status='pending'",
            (session_id, request_id),
        )

    def close(self) -> None:
        self.db.close()
