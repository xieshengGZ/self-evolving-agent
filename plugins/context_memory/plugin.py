"""Expose distilled long-term memory as a context section."""
from __future__ import annotations

from typing import Any, Dict, List

from app.kernel.api import PluginContext


def _memory(store: Any, system_prompt: str) -> List[Dict[str, Any]]:
    try:
        text = store.get_memory_text()
    except Exception:  # noqa: BLE001
        text = ""
    if not str(text).strip():
        return []
    return [
        {
            "kind": "memory",
            "role": "system",
            "label": "长期记忆（提炼知识）",
            "content": "## 长期记忆\n" + str(text),
            "priority": 600,
            "order": 15,
            "compressible": True,
        }
    ]


def setup(ctx: PluginContext) -> None:
    ctx.context.register(_memory, order=40)


def teardown(ctx: PluginContext) -> None:
    pass
