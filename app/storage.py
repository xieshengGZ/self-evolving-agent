"""SQLite persistence for sessions, messages, events, and plans.

The in-memory `Session` remains the hot path; this module is the durable
record. It uses one connection per operation so it is safe to call from the
agent thread, HTTP handlers, and the SSE stream at the same time.
"""
from __future__ import annotations

import json
import sqlite3
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Dict, Iterator, List, Optional

from app import config

SCHEMA_VERSION = "1"

_SCHEMA = """
CREATE TABLE IF NOT EXISTS meta (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS sessions (
    conv_id TEXT PRIMARY KEY,
    created_at REAL NOT NULL,
    updated_at REAL NOT NULL,
    title TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL DEFAULT 'idle',
    task TEXT NOT NULL DEFAULT '',
    task_state TEXT NOT NULL DEFAULT '{}'
);

CREATE TABLE IF NOT EXISTS messages (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    conv_id TEXT NOT NULL,
    role TEXT NOT NULL,
    content TEXT NOT NULL,
    ts REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_messages_conv ON messages(conv_id, id);

CREATE TABLE IF NOT EXISTS events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    conv_id TEXT NOT NULL,
    ts REAL NOT NULL,
    kind TEXT NOT NULL,
    message TEXT NOT NULL,
    extra TEXT NOT NULL DEFAULT '{}'
);
CREATE INDEX IF NOT EXISTS idx_events_conv ON events(conv_id, id);

CREATE TABLE IF NOT EXISTS plans (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    conv_id TEXT NOT NULL,
    goal TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'active',
    steps TEXT NOT NULL DEFAULT '[]',
    created_at REAL NOT NULL,
    updated_at REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_plans_conv ON plans(conv_id, id);
"""


class Storage:
    def __init__(self, path: Optional[str | Path] = None) -> None:
        self.path = Path(path) if path else Path(config.DB_PATH)

    @contextmanager
    def _db(self) -> Iterator[sqlite3.Connection]:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(str(self.path), timeout=10)
        conn.row_factory = sqlite3.Row
        try:
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("PRAGMA synchronous=NORMAL")
            conn.execute("PRAGMA busy_timeout=5000")
            yield conn
            conn.commit()
        finally:
            conn.close()

    def init(self) -> None:
        with self._db() as conn:
            conn.executescript(_SCHEMA)
            conn.execute(
                "INSERT INTO meta(key, value) VALUES('schema_version', ?) "
                "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                (SCHEMA_VERSION,),
            )

    # ---------------- sessions ----------------
    def create_session(self, conv_id: str, title: str = "") -> None:
        now = time.time()
        with self._db() as conn:
            conn.execute(
                "INSERT INTO sessions(conv_id, created_at, updated_at, title, status, task, task_state) "
                "VALUES(?, ?, ?, ?, 'idle', '', '{}') "
                "ON CONFLICT(conv_id) DO UPDATE SET updated_at=excluded.updated_at, "
                "title=CASE WHEN excluded.title <> '' THEN excluded.title ELSE sessions.title END",
                (conv_id, now, now, title or ""),
            )

    def save_session(
        self,
        conv_id: str,
        *,
        status: str,
        task: str,
        task_state: Dict[str, Any],
        title: str = "",
    ) -> None:
        now = time.time()
        with self._db() as conn:
            conn.execute(
                "INSERT INTO sessions(conv_id, created_at, updated_at, title, status, task, task_state) "
                "VALUES(?, ?, ?, ?, ?, ?, ?) "
                "ON CONFLICT(conv_id) DO UPDATE SET updated_at=excluded.updated_at, "
                "status=excluded.status, task=excluded.task, task_state=excluded.task_state, "
                "title=CASE WHEN excluded.title <> '' THEN excluded.title ELSE sessions.title END",
                (
                    conv_id,
                    now,
                    now,
                    title or "",
                    status,
                    task or "",
                    json.dumps(task_state or {}, ensure_ascii=False),
                ),
            )

    def list_sessions(self, limit: int = 50) -> List[Dict[str, Any]]:
        with self._db() as conn:
            rows = conn.execute(
                "SELECT conv_id, created_at, updated_at, title, status, task "
                "FROM sessions ORDER BY updated_at DESC LIMIT ?",
                (max(1, int(limit)),),
            ).fetchall()
        return [dict(row) for row in rows]

    def load_current(self, window: int = 8, event_limit: int = 500) -> Optional[Dict[str, Any]]:
        with self._db() as conn:
            session = conn.execute(
                "SELECT * FROM sessions ORDER BY updated_at DESC LIMIT 1"
            ).fetchone()
            if session is None:
                return None
            conv_id = session["conv_id"]
            message_rows = conn.execute(
                "SELECT role, content FROM messages WHERE conv_id=? ORDER BY id DESC LIMIT ?",
                (conv_id, max(1, int(window))),
            ).fetchall()
            event_rows = conn.execute(
                "SELECT id, ts, kind, message, extra FROM events "
                "WHERE conv_id=? ORDER BY id DESC LIMIT ?",
                (conv_id, max(1, int(event_limit))),
            ).fetchall()

        history = [{"role": row["role"], "content": row["content"]} for row in reversed(message_rows)]
        events = [self._event_from_row(row) for row in event_rows]
        events.reverse()
        try:
            task_state = json.loads(session["task_state"] or "{}")
        except Exception:  # noqa: BLE001
            task_state = {}
        return {
            "conv_id": conv_id,
            "created_at": session["created_at"],
            "updated_at": session["updated_at"],
            "title": session["title"],
            "status": session["status"],
            "task": session["task"],
            "task_state": task_state if isinstance(task_state, dict) else {},
            "history": history,
            "events": events,
        }

    # ---------------- messages ----------------
    def append_message(self, conv_id: str, role: str, content: str, ts: Optional[float] = None) -> None:
        with self._db() as conn:
            conn.execute(
                "INSERT INTO messages(conv_id, role, content, ts) VALUES(?, ?, ?, ?)",
                (conv_id, role, content, float(ts if ts is not None else time.time())),
            )

    # ---------------- events ----------------
    def append_event(
        self,
        conv_id: str,
        kind: str,
        message: str,
        extra: Optional[Dict[str, Any]] = None,
        ts: Optional[float] = None,
    ) -> int:
        with self._db() as conn:
            cursor = conn.execute(
                "INSERT INTO events(conv_id, ts, kind, message, extra) VALUES(?, ?, ?, ?, ?)",
                (
                    conv_id,
                    float(ts if ts is not None else time.time()),
                    kind,
                    message,
                    json.dumps(extra or {}, ensure_ascii=False),
                ),
            )
            return int(cursor.lastrowid or 0)

    def events_after(self, conv_id: str, last_id: int = 0, limit: int = 500) -> List[Dict[str, Any]]:
        with self._db() as conn:
            rows = conn.execute(
                "SELECT id, ts, kind, message, extra FROM events "
                "WHERE conv_id=? AND id>? ORDER BY id ASC LIMIT ?",
                (conv_id, max(0, int(last_id)), max(1, int(limit))),
            ).fetchall()
        return [self._event_from_row(row) for row in rows]

    def clear_events(self, conv_id: str) -> int:
        with self._db() as conn:
            cursor = conn.execute("DELETE FROM events WHERE conv_id=?", (conv_id,))
            return int(cursor.rowcount or 0)

    @staticmethod
    def _event_from_row(row: sqlite3.Row) -> Dict[str, Any]:
        event: Dict[str, Any] = {
            "id": row["id"],
            "ts": row["ts"],
            "kind": row["kind"],
            "message": row["message"],
        }
        try:
            extra = json.loads(row["extra"] or "{}")
        except Exception:  # noqa: BLE001
            extra = {}
        if isinstance(extra, dict):
            event.update(extra)
        return event

    # ---------------- plans ----------------
    def save_plan(
        self,
        conv_id: str,
        goal: str,
        steps: List[Dict[str, Any]],
        status: str = "active",
    ) -> int:
        now = time.time()
        with self._db() as conn:
            cursor = conn.execute(
                "INSERT INTO plans(conv_id, goal, status, steps, created_at, updated_at) "
                "VALUES(?, ?, ?, ?, ?, ?)",
                (
                    conv_id,
                    goal or "",
                    status or "active",
                    json.dumps(list(steps or []), ensure_ascii=False),
                    now,
                    now,
                ),
            )
            return int(cursor.lastrowid or 0)

    def list_plans(self, conv_id: str, limit: int = 20) -> List[Dict[str, Any]]:
        with self._db() as conn:
            rows = conn.execute(
                "SELECT id, conv_id, goal, status, steps, created_at, updated_at "
                "FROM plans WHERE conv_id=? ORDER BY id DESC LIMIT ?",
                (conv_id, max(1, int(limit))),
            ).fetchall()
        plans: List[Dict[str, Any]] = []
        for row in rows:
            item = dict(row)
            try:
                item["steps"] = json.loads(item.get("steps") or "[]")
            except Exception:  # noqa: BLE001
                item["steps"] = []
            plans.append(item)
        return plans

    # ---------------- migration ----------------
    def migrate_json(self, json_path: Optional[str | Path] = None) -> Dict[str, Any]:
        self.init()
        path = Path(json_path) if json_path else Path(config.SESSION_PATH)
        result: Dict[str, Any] = {
            "migrated": False,
            "source": str(path),
            "messages": 0,
            "events": 0,
            "plans": 0,
        }
        with self._db() as conn:
            already = conn.execute("SELECT value FROM meta WHERE key='json_migrated'").fetchone()
            if already is not None:
                result["reason"] = "already migrated"
                return result
            existing = conn.execute("SELECT COUNT(*) AS n FROM sessions").fetchone()["n"]
        if existing:
            self._set_meta("json_migrated", "skipped: database already has sessions")
            result["reason"] = "database already has sessions"
            return result
        if not path.is_file():
            result["reason"] = "session.json not found"
            return result

        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except Exception as exc:  # noqa: BLE001
            result["reason"] = f"invalid session.json: {exc}"
            return result
        if not isinstance(data, dict):
            result["reason"] = "session.json is not an object"
            return result

        conv_id = str(data.get("conv_id") or "").strip() or f"legacy-{int(time.time())}"
        history = data.get("history") or []
        events = data.get("events") or []
        task_state = data.get("task_state") if isinstance(data.get("task_state"), dict) else {}

        now = time.time()
        with self._db() as conn:
            conn.execute(
                "INSERT OR REPLACE INTO sessions"
                "(conv_id, created_at, updated_at, title, status, task, task_state) "
                "VALUES(?, ?, ?, ?, ?, ?, ?)",
                (
                    conv_id,
                    now,
                    now,
                    str(data.get("task") or "")[:120],
                    "idle" if data.get("status") == "running" else str(data.get("status") or "idle"),
                    str(data.get("task") or ""),
                    json.dumps(task_state, ensure_ascii=False),
                ),
            )
            if isinstance(history, list):
                for index, item in enumerate(history):
                    if not isinstance(item, dict):
                        continue
                    conn.execute(
                        "INSERT INTO messages(conv_id, role, content, ts) VALUES(?, ?, ?, ?)",
                        (
                            conv_id,
                            str(item.get("role") or "user"),
                            item.get("content") if isinstance(item.get("content"), str) else json.dumps(item.get("content"), ensure_ascii=False),
                            now + index * 0.001,
                        ),
                    )
                    result["messages"] += 1
            if isinstance(events, list):
                for index, item in enumerate(events):
                    if not isinstance(item, dict):
                        continue
                    extra = {
                        key: value
                        for key, value in item.items()
                        if key not in ("id", "ts", "kind", "message")
                    }
                    conn.execute(
                        "INSERT INTO events(conv_id, ts, kind, message, extra) VALUES(?, ?, ?, ?, ?)",
                        (
                            conv_id,
                            float(item.get("ts") or (now + index * 0.001)),
                            str(item.get("kind") or "event"),
                            item.get("message")
                            if isinstance(item.get("message"), str)
                            else json.dumps(item.get("message"), ensure_ascii=False),
                            json.dumps(extra, ensure_ascii=False),
                        ),
                    )
                    result["events"] += 1

        plan = task_state.get("plan") or []
        plan_text = task_state.get("plan_text") or ""
        if plan:
            self.save_plan(
                conv_id,
                goal=str(data.get("task") or ""),
                steps=plan if isinstance(plan, list) else [],
                status="active",
            )
            result["plans"] += 1
        elif plan_text:
            self.save_plan(conv_id, goal=str(data.get("task") or ""), steps=[{"text": plan_text}], status="active")
            result["plans"] += 1

        self._set_meta("json_migrated", str(path))
        result["migrated"] = True
        result["conv_id"] = conv_id
        return result

    # ---------------- meta ----------------
    def _set_meta(self, key: str, value: str) -> None:
        with self._db() as conn:
            conn.execute(
                "INSERT INTO meta(key, value) VALUES(?, ?) "
                "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                (key, value),
            )

    def get_meta(self, key: str) -> Optional[str]:
        with self._db() as conn:
            row = conn.execute("SELECT value FROM meta WHERE key=?", (key,)).fetchone()
        return row["value"] if row else None

    def totals(self) -> Dict[str, int]:
        self.init()
        with self._db() as conn:
            return {
                "sessions": int(conn.execute("SELECT COUNT(*) AS n FROM sessions").fetchone()["n"]),
                "messages": int(conn.execute("SELECT COUNT(*) AS n FROM messages").fetchone()["n"]),
                "events": int(conn.execute("SELECT COUNT(*) AS n FROM events").fetchone()["n"]),
                "plans": int(conn.execute("SELECT COUNT(*) AS n FROM plans").fetchone()["n"]),
            }
