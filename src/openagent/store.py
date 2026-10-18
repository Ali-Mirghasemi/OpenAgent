"""Small SQLite session journal with explicit pending/unknown outcome handling."""
from __future__ import annotations

import json
import hashlib
import sqlite3
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .models import Message, ProviderOutput


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
                messages TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS turns (
                session_id TEXT NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
                request_id TEXT NOT NULL,
                status TEXT NOT NULL,
                response TEXT,
                payload_hash TEXT,
                PRIMARY KEY (session_id, request_id)
            );
        """)

    def create(self, model: str) -> dict[str, Any]:
        sid, now = uuid.uuid4().hex, utcnow()
        self.db.execute(
            "INSERT INTO sessions VALUES (?,?,?,?,?)",
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
        }

    def claim_turn(self, session_id: str, request_id: str, message: Message) -> dict[str, Any] | None:
        """Return a cached completed response or reserve new work exactly once.

        Pending and unknown-outcome turns are NOT retried automatically: a transport can
        succeed remotely just before local recording fails.
        """
        payload_hash = hashlib.sha256(json.dumps(message.model_dump(mode="json", exclude_none=True), sort_keys=True).encode()).hexdigest()
        with self.db:
            self.db.execute("BEGIN IMMEDIATE")
            if not self.db.execute("SELECT 1 FROM sessions WHERE id=?", (session_id,)).fetchone():
                raise SessionNotFound(session_id)
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

    def complete_turn(self, session_id: str, request_id: str, user: Message, output: ProviderOutput) -> dict[str, Any]:
        """Atomically commit both transcript messages and a replayable reply."""
        payload = output.model_dump(mode="json")
        with self.db:
            self.db.execute("BEGIN IMMEDIATE")
            row = self.db.execute("SELECT messages FROM sessions WHERE id=?", (session_id,)).fetchone()
            if row is None:
                raise SessionNotFound(session_id)
            entries = json.loads(row["messages"])
            entries.append(user.model_dump(mode="json", exclude_none=True))
            entries.append(Message(role="assistant", content=output.content, tool_calls=output.tool_calls).model_dump(mode="json", exclude_none=True))
            self.db.execute(
                "UPDATE sessions SET messages=?,updated_at=? WHERE id=?",
                (json.dumps(entries), utcnow(), session_id),
            )
            self.db.execute(
                "UPDATE turns SET status='done',response=? WHERE session_id=? AND request_id=? AND status='pending'",
                (json.dumps(payload), session_id, request_id),
            )
        return payload

    def fail_turn(self, session_id: str, request_id: str) -> None:
        self.db.execute(
            "UPDATE turns SET status='unknown' WHERE session_id=? AND request_id=? AND status='pending'",
            (session_id, request_id),
        )

    def close(self) -> None:
        self.db.close()
