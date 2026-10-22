"""Planning plugin: turns a task into a typed, verifiable plan.

The kernel owns the Plan model and state machine. This plugin only owns the
LLM prompt/parse layer, so it can be hot-replaced without touching the loop.
"""
from __future__ import annotations

import json
import re
from typing import Any, Dict, List, Optional

from app.kernel.api import PluginContext

PLAN_SYSTEM = """你是任务规划器。根据用户任务输出可执行、可验收的计划。

只返回一个 JSON 对象，不要解释，不要 markdown 围栏。格式：
{
  "reasoning": "一句话整体思路",
  "acceptance": [{"kind": "file_exists", "path": "相对路径"}],
  "steps": [
    {
      "id": 1,
      "action": "shell",
      "args": {"command": "pwd"},
      "expected": "这一步要得到什么",
      "acceptance": [],
      "depends_on": []
    }
  ]
}

规则：
- 只有用户明确要求执行操作或产出文件时才规划；纯解释/问答不要生成步骤。
- action 只能从提供的可用 action 中选择；每步只做一件事，最多 8 步。
- 不要自行发明用户未要求的交付物；acceptance 只验收用户明确要求或步骤确实产出的对象。
- expected 是一句话验收目标；简单任务不要过度拆分。
- 能确定性验收时，在 acceptance 中给出检查；没有就不写。
- acceptance.kind 支持：
  - file_exists / file_not_empty：{"kind":"file_exists","path":"..."}
  - contains：{"kind":"contains","path":"...","text":"..."}
  - command / test：{"kind":"test","command":"pytest -q"}，退出码 0 通过
  - exit_code：{"kind":"exit_code","command":"...","expected_code":0}
  - manual：仅记录，不自动判定
- 不要规划 done 动作；内核会在计划完成后验收。
"""


def _strip_fence(text: str) -> str:
    text = text.strip()
    if not text.startswith("```"):
        return text
    first = text.find("\n")
    if first >= 0:
        text = text[first + 1 :]
    if text.rstrip().endswith("```"):
        text = text.rstrip()[:-3]
    return text.strip()


def _parse_reply(reply: Any) -> Dict[str, Any]:
    if isinstance(reply, dict):
        return reply
    if not isinstance(reply, str):
        return {"error": "planner reply is not a string or object"}
    text = _strip_fence(reply)
    try:
        obj = json.loads(text)
    except Exception:  # noqa: BLE001
        match = re.search(r"\{.*\}", text, re.DOTALL)
        if match is None:
            return {"error": "planner reply is not valid JSON"}
        try:
            obj = json.loads(match.group(0))
        except Exception as exc:  # noqa: BLE001
            return {"error": f"planner JSON parse failed: {exc}"}
    if not isinstance(obj, dict):
        return {"error": "planner reply must be a JSON object"}
    return obj


class LLMPlanner:
    def __init__(self, ctx: PluginContext) -> None:
        self.ctx = ctx
        try:
            self.max_steps = max(1, int(ctx.config.get("max_steps", 8)))
        except (TypeError, ValueError):
            self.max_steps = 8

    def _chat(self, system: str, user: str) -> Dict[str, Any]:
        try:
            reply = self.ctx.llm.chat(
                [
                    {"role": "system", "content": system},
                    {"role": "user", "content": user},
                ]
            )
        except Exception as exc:  # noqa: BLE001
            return {"error": f"{type(exc).__name__}: {exc}"}
        return _parse_reply(reply)

    def _prompt(self, task: str, actions: Optional[List[str]]) -> str:
        text = "任务：" + task.strip()
        if actions:
            text += "\n可用 action：" + ", ".join(str(item) for item in actions)
        else:
            text += "\n可用 action：shell, read_file, write_file"
        return text

    def _normalize(self, obj: Dict[str, Any]) -> Dict[str, Any]:
        if obj.get("error"):
            return obj
        steps = obj.get("steps") or []
        if not isinstance(steps, list) or not steps:
            return {"error": "empty plan"}
        normalized: List[Dict[str, Any]] = []
        for index, item in enumerate(steps[: self.max_steps], 1):
            if not isinstance(item, dict):
                continue
            step = dict(item)
            try:
                step["id"] = int(step.get("id", index))
            except (TypeError, ValueError):
                step["id"] = index
            step["action"] = str(step.get("action") or "")
            if not isinstance(step.get("args"), dict):
                step["args"] = {}
            step["expected"] = str(step.get("expected") or "")
            step["acceptance"] = (
                list(step["acceptance"]) if isinstance(step.get("acceptance"), list) else []
            )
            step["depends_on"] = (
                list(step["depends_on"]) if isinstance(step.get("depends_on"), list) else []
            )
            normalized.append(step)
        if not normalized:
            return {"error": "empty plan"}
        return {
            "reasoning": str(obj.get("reasoning") or "")[:2000],
            "acceptance": obj.get("acceptance") if isinstance(obj.get("acceptance"), list) else [],
            "steps": normalized,
        }

    def create(self, task: str, actions: Optional[List[str]] = None) -> Dict[str, Any]:
        if not task or not task.strip():
            return {"error": "empty task"}
        return self._normalize(self._chat(PLAN_SYSTEM, self._prompt(task, actions)))

    def revise(
        self,
        plan: Dict[str, Any],
        feedback: str,
        actions: Optional[List[str]] = None,
    ) -> Dict[str, Any]:
        try:
            previous = json.dumps(plan, ensure_ascii=False)[:8000]
        except Exception:  # noqa: BLE001
            previous = str(plan)[:8000]
        user = (
            "原计划：\n"
            + previous
            + "\n\n失败/验收反馈：\n"
            + (feedback or "")[:2000]
            + "\n\n请保持原目标，修正步骤后重新给出完整计划。"
        )
        if actions:
            user += "\n可用 action：" + ", ".join(str(item) for item in actions)
        return self._normalize(self._chat(PLAN_SYSTEM, user))


def _status(payload: Dict[str, Any], ctx: PluginContext) -> Dict[str, Any]:
    state = ctx.store.get_task_state() or {}
    return {
        "ok": True,
        "plan": state.get("plan"),
        "plan_text": state.get("plan_text", ""),
    }


def _reset(payload: Dict[str, Any], ctx: PluginContext) -> Dict[str, Any]:
    state = dict(ctx.store.get_task_state() or {})
    plan = state.get("plan")
    plan_id = plan.get("id") if isinstance(plan, dict) else 0
    if plan_id:
        try:
            ctx.store.update_plan(int(plan_id), status="cancelled")
        except Exception:  # noqa: BLE001
            pass
    for key in ("plan", "plan_text", "plan_status", "plan_goal", "current"):
        state.pop(key, None)
    state["plan_reset_reason"] = "http reset"
    ctx.store.set_task_state(state)
    return {"ok": True, "reset": True}


def setup(ctx: PluginContext) -> None:
    ctx.planners.register("default", LLMPlanner(ctx))
    ctx.http.register("status", lambda payload: _status(payload, ctx))
    ctx.http.register("reset", lambda payload: _reset(payload, ctx))


def teardown(ctx: PluginContext) -> None:
    pass
