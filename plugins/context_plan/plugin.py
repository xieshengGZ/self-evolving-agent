"""Expose the current plan text as a context section."""
from __future__ import annotations

from typing import Any, Dict, List

from app.kernel.api import PluginContext


def _plan(store: Any, system_prompt: str) -> List[Dict[str, Any]]:
    state = store.get_task_state() or {}
    text = state.get("plan_text") or ""
    if not str(text).strip():
        return []
    return [
        {
            "kind": "plan",
            "role": "system",
            "label": "执行计划",
            "content": "## 执行计划\n" + str(text),
            "priority": 700,
            "order": 20,
            "compressible": True,
        }
    ]


def setup(ctx: PluginContext) -> None:
    ctx.context.register(_plan, order=30)


def teardown(ctx: PluginContext) -> None:
    pass
