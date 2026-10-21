"""Base context sections: system prompt and current task."""
from __future__ import annotations

from typing import Any, Dict, List

from app.kernel.api import PluginContext


def _base(store: Any, system_prompt: str) -> List[Dict[str, Any]]:
    return [
        {
            "kind": "system",
            "role": "system",
            "label": "系统提示词 prompts/system.md",
            "content": system_prompt,
            "priority": 1000,
            "order": 0,
            "required": True,
            "compressible": False,
        }
    ]


def _task(store: Any, system_prompt: str) -> List[Dict[str, Any]]:
    task = (store.get_task() or "").strip()
    if not task:
        return []
    return [
        {
            "kind": "task",
            "role": "user",
            "label": "当前任务",
            "content": "当前任务：" + task,
            "priority": 900,
            "order": 10,
            "required": True,
            "compressible": True,
        }
    ]


def setup(ctx: PluginContext) -> None:
    ctx.context.register(_base, order=10)
    ctx.context.register(_task, order=20)


def teardown(ctx: PluginContext) -> None:
    pass
