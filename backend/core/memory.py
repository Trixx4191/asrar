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

        # Plan — the agent's current checklist for this conversation
        # (mirrors Claude Code's TodoWrite). One row, overwritten on each update_plan call.
        c.execute("""
            CREATE TABLE IF NOT EXISTS plans (
                conversation_id TEXT PRIMARY KEY REFERENCES conversations(id) ON DELETE CASCADE,
                items           TEXT NOT NULL,
                updated_at      TEXT NOT NULL
            )
        """)

        # Verification state — tracks whether code files were changed since
        # the last run_tests/execute_code call, so the agent loop can nudge
        # the model to verify its own work before declaring a turn done
        # instead of trusting that a successful write_file/edit_file means
        # the code actually works.
        c.execute("""
            CREATE TABLE IF NOT EXISTS verification_state (
                conversation_id TEXT PRIMARY KEY REFERENCES conversations(id) ON DELETE CASCADE,
                dirty           INTEGER NOT NULL DEFAULT 0,
                dirty_files     TEXT,
                last_result     TEXT,
                nudged          INTEGER NOT NULL DEFAULT 0,
                updated_at      TEXT NOT NULL
            )
        """)


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


# ─────────────────────────────────────────────────────────────
# Plan — the agent's current step-by-step checklist for this
# conversation (mirrors Claude Code's TodoWrite tool). One row
# per conversation; each update_plan tool call overwrites it.
# ─────────────────────────────────────────────────────────────

def set_plan(conversation_id: str, items: list[dict]) -> None:
    with _conn() as c:
        c.execute(
            """INSERT INTO plans (conversation_id, items, updated_at)
               VALUES (?, ?, ?)
               ON CONFLICT(conversation_id) DO UPDATE SET
                   items = excluded.items,
                   updated_at = excluded.updated_at""",
            (conversation_id, json.dumps(items), _now()),
        )


def get_plan(conversation_id: str) -> list[dict]:
    with _conn() as c:
        row = c.execute(
            "SELECT items FROM plans WHERE conversation_id = ?",
            (conversation_id,),
        ).fetchone()
    if not row:
        return []
    try:
        return json.loads(row["items"])
    except (json.JSONDecodeError, TypeError):
        return []


# ─────────────────────────────────────────────────────────────
# Verification state — has code changed since the last test run?
# Lets the agent loop enforce "verify before you say you're done"
# instead of just hoping the model remembers to check its work.
# ─────────────────────────────────────────────────────────────

def mark_dirty(conversation_id: str, file_path: str) -> None:
    """Record that a code file was written/edited and hasn't been verified
    since. Resets `nudged` so a fresh change earns a fresh chance to prompt
    the model to check it, even within the same turn."""
    with _conn() as c:
        row = c.execute(
            "SELECT dirty_files FROM verification_state WHERE conversation_id = ?",
            (conversation_id,),
        ).fetchone()
        files: list[str] = []
        if row and row["dirty_files"]:
            try:
                files = json.loads(row["dirty_files"])
            except (json.JSONDecodeError, TypeError):
                files = []
        if file_path not in files:
            files.append(file_path)
        c.execute(
            """INSERT INTO verification_state
                   (conversation_id, dirty, dirty_files, nudged, updated_at)
               VALUES (?, 1, ?, 0, ?)
               ON CONFLICT(conversation_id) DO UPDATE SET
                   dirty       = 1,
                   dirty_files = excluded.dirty_files,
                   nudged      = 0,
                   updated_at  = excluded.updated_at""",
            (conversation_id, json.dumps(files), _now()),
        )


def mark_verified(conversation_id: str, result: dict | None = None) -> None:
    """Clear the dirty flag after a run_tests/execute_code call, regardless
    of whether that check passed — the point is that verification happened,
    not that it succeeded (a failed check should surface in the response,
    not loop forever)."""
    with _conn() as c:
        c.execute(
            """INSERT INTO verification_state
                   (conversation_id, dirty, dirty_files, last_result, nudged, updated_at)
               VALUES (?, 0, '[]', ?, 0, ?)
               ON CONFLICT(conversation_id) DO UPDATE SET
                   dirty       = 0,
                   dirty_files = '[]',
                   last_result = excluded.last_result,
                   nudged      = 0,
                   updated_at  = excluded.updated_at""",
            (conversation_id, json.dumps(result) if result else None, _now()),
        )


def set_nudged(conversation_id: str) -> None:
    with _conn() as c:
        c.execute(
            """INSERT INTO verification_state (conversation_id, dirty, nudged, updated_at)
               VALUES (?, 1, 1, ?)
               ON CONFLICT(conversation_id) DO UPDATE SET
                   nudged     = 1,
                   updated_at = excluded.updated_at""",
            (conversation_id, _now()),
        )


def get_verification_state(conversation_id: str) -> dict:
    with _conn() as c:
        row = c.execute(
            "SELECT * FROM verification_state WHERE conversation_id = ?",
            (conversation_id,),
        ).fetchone()
    if not row:
        return {"dirty": False, "dirty_files": [], "last_result": None, "nudged": False}
    d = dict(row)
    d["dirty"] = bool(d["dirty"])
    d["nudged"] = bool(d["nudged"])
    try:
        d["dirty_files"] = json.loads(d["dirty_files"]) if d["dirty_files"] else []
    except (json.JSONDecodeError, TypeError):
        d["dirty_files"] = []
    try:
        d["last_result"] = json.loads(d["last_result"]) if d["last_result"] else None
    except (json.JSONDecodeError, TypeError):
        d["last_result"] = None
    return d


# Initialize on import so every module that touches memory gets the schema.
init_db()
