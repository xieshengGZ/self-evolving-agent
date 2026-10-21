"""In-process event bus.

Subscribers are intentionally best-effort: a broken hook must not break the
agent loop. Persistence is opt-in so the CLI can inspect plugins without
writing to the live session.
"""
from __future__ import annotations

import json
import logging
import threading
from typing import Any, Callable, List, Optional

from app.kernel.store import Store

log = logging.getLogger("kernel.bus")

Subscriber = Callable[[str, Any], None]


class EventBus:
    def __init__(self, store: Optional[Store] = None) -> None:
        self._store = store
        self._subscribers: List[Subscriber] = []
        self._lock = threading.RLock()

    def subscribe(self, subscriber: Subscriber) -> None:
        with self._lock:
            self._subscribers.append(subscriber)

    def unsubscribe(self, subscriber: Subscriber) -> None:
        with self._lock:
            self._subscribers = [s for s in self._subscribers if s is not subscriber]

    def publish(self, topic: str, payload: Any = None, *, persist: bool = False) -> None:
        if persist and self._store is not None:
            message = payload if isinstance(payload, str) else json.dumps(payload, ensure_ascii=False)
            try:
                self._store.add_event(topic, message)
            except Exception:  # noqa: BLE001
                log.exception("failed to persist event %s", topic)
        with self._lock:
            subscribers = list(self._subscribers)
        for subscriber in subscribers:
            try:
                subscriber(topic, payload)
            except Exception:  # noqa: BLE001
                log.exception("subscriber failed for %s", topic)
