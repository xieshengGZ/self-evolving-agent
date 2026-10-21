"""Expose the recent conversation window as one section per message."""
from __future__ import annotations

from typing import Any, Dict, List

from app.kernel.api import PluginContext


def _history(store: Any, system_prompt: str) -> List[Dict[str, Any]]:
    try:
        history = store.get_history()
    except Exception:  # noqa: BLE001
        history = []
    sections: List[Dict[str, Any]] = []
    for index, message in enumerate(history):
        if not isinstance(message, dict):
            continue
        sections.append(
            {
                "kind": "history",
                "role": str(message.get("role") or "user"),
                "label": f"历史消息 {index + 1}",
                "content": message.get("content", ""),
                "priority": 500 + index,
                "order": 100 + index,
                "compressible": True,
            }
        )
    return sections


def setup(ctx: PluginContext) -> None:
    ctx.context.register(_history, order=50)


def teardown(ctx: PluginContext) -> None:
    pass
