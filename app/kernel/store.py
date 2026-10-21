"""Storage seam.

Phase 1 keeps the legacy session module behind this interface. When the
SQLite migration lands, only this file changes; plugins keep using Store.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from app import session, storage as storage_mod


class Store:
    def __init__(self, storage_backend: Optional[storage_mod.Storage] = None) -> None:
        self.storage = storage_backend or storage_mod.Storage()

    def add_event(self, kind: str, message: Any, **extra: Any) -> None:
        session.add_event(kind, message, **extra)

    def get_events(self, since: int = 0) -> List[Dict[str, Any]]:
        return session.get_events(since=since)

    def clear_events(self) -> None:
        session.clear_events()

    def event_count(self) -> int:
        return session.event_count()

    def append_history(self, role: str, content: str) -> None:
        session.append_history(role, content)

    def get_history(self) -> List[Dict[str, str]]:
        return session.get_history()

    def persist(self) -> None:
        session.persist()

    def load(self) -> None:
        session.load()

    def get_status(self) -> str:
        return session.get_status()

    def set_status(self, status: str) -> None:
        session.set_status(status)

    def is_cancelled(self) -> bool:
        return session.is_cancelled()

    def get_conv_id(self) -> str:
        return session.get_conv_id()

    def get_task(self) -> str:
        return session.get_task()

    def set_task(self, task: str) -> None:
        session.set_task(task)

    def get_task_state(self) -> Dict[str, Any]:
        return session.get_task_state()

    def set_task_state(self, state: Dict[str, Any]) -> None:
        session.set_task_state(state)

    def new_conversation(self, task: str) -> str:
        return session.new_conversation(task)

    def snapshot(self) -> Dict[str, Any]:
        return session.snapshot()

    def record_step(self, action: Dict[str, Any], result: Dict[str, Any]) -> None:
        session.record_step(action, result)

    def record_prompt(self, prompt: str) -> None:
        session.record_prompt(prompt)

    # ---------------- durable queries ----------------
    def last_event_id(self) -> int:
        return session.last_event_id()

    def wait_events(self, last_id: int = 0, timeout: float = 15.0) -> List[Dict[str, Any]]:
        return session.wait_events(last_id, timeout)

    def list_sessions(self, limit: int = 50) -> List[Dict[str, Any]]:
        return self.storage.list_sessions(limit)

    def save_plan(
        self,
        conv_id: str,
        goal: str,
        steps: List[Dict[str, Any]],
        status: str = "active",
    ) -> int:
        return self.storage.save_plan(conv_id, goal, steps, status)

    def get_plans(self, conv_id: str = "", limit: int = 20) -> List[Dict[str, Any]]:
        return self.storage.list_plans(conv_id or session.get_conv_id(), limit)

    def db_path(self) -> str:
        return str(self.storage.path)
