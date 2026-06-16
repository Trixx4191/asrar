"""
Asrār — Conversation Memory
backend/core/memory.py

Persistent, multi-conversation memory backed by SQLite.
Replaces the old behavior where chat history only lived in the
frontend's React state (and vanished on refresh/restart).

Schema:
    conversations(id, title, created_at, updated_at)
    messages(id, conversation_id, role, content, model, task_type, tool_calls, created_at)

Public API:
    init_db()
    create_conversation(title=None) -> str
    list_conversations() -> list[dict]
    get_conversation(conversation_id) -> dict | None
    get_messages(conversation_id) -> list[Message]          # for feeding the agent loop
    get_messages_full(conversation_id) -> list[dict]         # for the frontend (includes model/tool metadata)
    add_message(conversation_id, role, content, model=None, task_type=None, tool_calls=None) -> int
    rename_conversation(conversation_id, title) -> bool
    delete_conversation(conversation_id) -> bool
    touch_conversation(conversation_id) -> None
    auto_title(first_message) -> str
"""

from __future__ import annotations

import json
import sqlite3
import uuid
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path

import sys
ROOT = Path(__file__).parent.parent.parent
sys.path.insert(0, str(ROOT / "backend"))

from providers.base import Message

DB_PATH = ROOT / "data" / "asrar.db"


@contextmanager
def _conn():
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def init_db() -> None:
    with _conn() as c:
        c.execute("""
            CREATE TABLE IF NOT EXISTS conversations (
                id          TEXT PRIMARY KEY,
                title       TEXT NOT NULL,
                created_at  TEXT NOT NULL,
                updated_at  TEXT NOT NULL
            )
        """)
        c.execute("""
            CREATE TABLE IF NOT EXISTS messages (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                conversation_id TEXT NOT NULL REFERENCES conversations(id) ON DELETE CASCADE,
                role            TEXT NOT NULL,
                content         TEXT NOT NULL,
                model           TEXT,
                task_type       TEXT,
                tool_calls      TEXT,
                created_at      TEXT NOT NULL
            )
        """)
        c.execute("CREATE INDEX IF NOT EXISTS idx_messages_conv ON messages(conversation_id)")

        # Supervisor state: one row per conversation, tracks whether the
        # last assistant turn was an unresolved clarifying question, and
        # if so, which model asked it (so the next message stays sticky).
        c.execute("""
            CREATE TABLE IF NOT EXISTS conversation_state (
                conversation_id        TEXT PRIMARY KEY REFERENCES conversations(id) ON DELETE CASCADE,
                awaiting_clarification INTEGER NOT NULL DEFAULT 0,
                pending_model_key      TEXT,
                pending_provider       TEXT,
                pending_display_name   TEXT,
                task_type              TEXT,
                updated_at             TEXT NOT NULL
            )
        """)

        # Supervisor execution log: a row per routing decision / tool call /
        # state transition, so a conversation's behavior is inspectable.
        c.execute("""
            CREATE TABLE IF NOT EXISTS execution_log (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                conversation_id TEXT NOT NULL REFERENCES conversations(id) ON DELETE CASCADE,
                event           TEXT NOT NULL,
                model           TEXT,
                detail          TEXT,
                created_at      TEXT NOT NULL
            )
        """)
        c.execute("CREATE INDEX IF NOT EXISTS idx_exec_log_conv ON execution_log(conversation_id)")


def _now() -> str:
    return datetime.now().isoformat()


def auto_title(first_message: str, max_len: int = 48) -> str:
    text = " ".join((first_message or "New chat").split())
    return text[:max_len] + ("…" if len(text) > max_len else "")


def create_conversation(title: str | None = None) -> str:
    conv_id = uuid.uuid4().hex[:12]
    ts = _now()
    with _conn() as c:
        c.execute(
            "INSERT INTO conversations (id, title, created_at, updated_at) VALUES (?, ?, ?, ?)",
            (conv_id, title or "New chat", ts, ts),
        )
    return conv_id


def list_conversations() -> list[dict]:
    with _conn() as c:
        rows = c.execute("""
            SELECT conv.id, conv.title, conv.created_at, conv.updated_at,
                   (SELECT COUNT(*) FROM messages m WHERE m.conversation_id = conv.id) AS message_count
            FROM conversations conv
            ORDER BY conv.updated_at DESC
        """).fetchall()
    return [dict(r) for r in rows]


def get_conversation(conversation_id: str) -> dict | None:
    with _conn() as c:
        row = c.execute(
            "SELECT id, title, created_at, updated_at FROM conversations WHERE id = ?",
            (conversation_id,),
        ).fetchone()
    return dict(row) if row else None


def get_messages(conversation_id: str) -> list[Message]:
    """History in the simple {role, content} form the agent loop expects."""
    with _conn() as c:
        rows = c.execute(
            "SELECT role, content FROM messages WHERE conversation_id = ? ORDER BY id ASC",
            (conversation_id,),
        ).fetchall()
    return [Message(role=r["role"], content=r["content"]) for r in rows]


def get_messages_full(conversation_id: str) -> list[dict]:
    """History with model/task_type/tool_calls metadata, for re-hydrating the frontend UI."""
    with _conn() as c:
        rows = c.execute(
            """SELECT role, content, model, task_type, tool_calls, created_at
               FROM messages WHERE conversation_id = ? ORDER BY id ASC""",
            (conversation_id,),
        ).fetchall()
    out = []
    for r in rows:
        d = dict(r)
        try:
            d["tool_calls"] = json.loads(d["tool_calls"]) if d["tool_calls"] else []
        except (json.JSONDecodeError, TypeError):
            d["tool_calls"] = []
        out.append(d)
    return out


def add_message(
    conversation_id: str,
    role: str,
    content: str,
    model: str | None = None,
    task_type: str | None = None,
    tool_calls: list | None = None,
) -> int:
    with _conn() as c:
        cur = c.execute(
            """INSERT INTO messages (conversation_id, role, content, model, task_type, tool_calls, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (
                conversation_id, role, content, model, task_type,
                json.dumps(tool_calls) if tool_calls else None,
                _now(),
            ),
        )
        c.execute("UPDATE conversations SET updated_at = ? WHERE id = ?", (_now(), conversation_id))
        return cur.lastrowid


def touch_conversation(conversation_id: str) -> None:
    with _conn() as c:
        c.execute("UPDATE conversations SET updated_at = ? WHERE id = ?", (_now(), conversation_id))


def rename_conversation(conversation_id: str, title: str) -> bool:
    with _conn() as c:
        cur = c.execute(
            "UPDATE conversations SET title = ?, updated_at = ? WHERE id = ?",
            (title, _now(), conversation_id),
        )
        return cur.rowcount > 0


def delete_conversation(conversation_id: str) -> bool:
    with _conn() as c:
        cur = c.execute("DELETE FROM conversations WHERE id = ?", (conversation_id,))
        return cur.rowcount > 0


# ─────────────────────────────────────────────────────────────
# Supervisor state — sticky routing across an open clarification
# ─────────────────────────────────────────────────────────────

def get_state(conversation_id: str) -> dict | None:
    with _conn() as c:
        row = c.execute(
            "SELECT * FROM conversation_state WHERE conversation_id = ?",
            (conversation_id,),
        ).fetchone()
    if not row:
        return None
    d = dict(row)
    d["awaiting_clarification"] = bool(d["awaiting_clarification"])
    return d


def set_awaiting(
    conversation_id: str,
    model_key: str,
    provider: str,
    display_name: str,
    task_type: str | None,
) -> None:
    with _conn() as c:
        c.execute(
            """INSERT INTO conversation_state
                   (conversation_id, awaiting_clarification, pending_model_key,
                    pending_provider, pending_display_name, task_type, updated_at)
               VALUES (?, 1, ?, ?, ?, ?, ?)
               ON CONFLICT(conversation_id) DO UPDATE SET
                   awaiting_clarification = 1,
                   pending_model_key      = excluded.pending_model_key,
                   pending_provider       = excluded.pending_provider,
                   pending_display_name   = excluded.pending_display_name,
                   task_type              = excluded.task_type,
                   updated_at             = excluded.updated_at""",
            (conversation_id, model_key, provider, display_name, task_type, _now()),
        )


def clear_awaiting(conversation_id: str) -> None:
    with _conn() as c:
        c.execute(
            """INSERT INTO conversation_state (conversation_id, awaiting_clarification, updated_at)
               VALUES (?, 0, ?)
               ON CONFLICT(conversation_id) DO UPDATE SET
                   awaiting_clarification = 0,
                   updated_at = excluded.updated_at""",
            (conversation_id, _now()),
        )


# ─────────────────────────────────────────────────────────────
# Execution log — visibility into every routing/tool decision
# ─────────────────────────────────────────────────────────────

def log_event(conversation_id: str, event: str, detail: dict | None = None, model: str | None = None) -> None:
    with _conn() as c:
        c.execute(
            "INSERT INTO execution_log (conversation_id, event, model, detail, created_at) VALUES (?, ?, ?, ?, ?)",
            (conversation_id, event, model, json.dumps(detail) if detail else None, _now()),
        )


def get_log(conversation_id: str, limit: int = 200) -> list[dict]:
    with _conn() as c:
        rows = c.execute(
            """SELECT event, model, detail, created_at FROM execution_log
               WHERE conversation_id = ? ORDER BY id ASC LIMIT ?""",
            (conversation_id, limit),
        ).fetchall()
    out = []
    for r in rows:
        d = dict(r)
        try:
            d["detail"] = json.loads(d["detail"]) if d["detail"] else {}
        except (json.JSONDecodeError, TypeError):
            d["detail"] = {}
        out.append(d)
    return out


# Initialize on import so every module that touches memory gets the schema.
init_db()
