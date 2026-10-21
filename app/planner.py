"""任务规划器：在 ReAct 循环前生成执行计划。"""
import json
from app import deepseek

PLAN_SYSTEM = """你是任务规划器。根据用户任务，输出执行计划。

只返回一个 JSON 对象，不要解释，不要 markdown 围栏。格式：
{"reasoning": "一句话说明整体思路", "steps": [{"id": 1, "action": "shell", "args": {"command": "pwd"}, "expected": "当前目录路径"}]}

规则：
- action 只能是 shell / write_file / read_file / read_symbol / edit_symbol
- 每步只做一件事，最多 8 步
- expected 是一句话说明这一步要得到什么
- 简单任务（1-2 步）直接给，不要过度拆分"""


def plan(task: str, max_steps: int = 8) -> dict:
    if not task or not task.strip():
        return {"error": "empty task"}
    try:
        reply = deepseek.chat([
            {"role": "system", "content": PLAN_SYSTEM},
            {"role": "user", "content": "任务：" + task.strip()},
        ])
        if isinstance(reply, dict):
            obj = reply
        else:
            text = str(reply).strip()
            if text.startswith("```"):
                nl = text.find("\n")
                if nl > 0:
                    text = text[nl + 1:]
                if text.rstrip().endswith("```"):
                    text = text.rstrip()[:-3]
            obj = json.loads(text.strip())
        steps = obj.get("steps") or []
        if not isinstance(steps, list) or not steps:
            return {"error": "empty plan"}
        return {"steps": steps[:max_steps], "reasoning": str(obj.get("reasoning", ""))[:200]}
    except Exception as e:
        return {"error": type(e).__name__ + ": " + str(e)}


def format_plan_text(steps: list) -> str:
    lines = []
    for s in steps:
        sid = s.get("id", "?")
        act = s.get("action", "?")
        exp = s.get("expected", "")
        dep = s.get("depends_on") or []
        dep_str = " (依赖 " + str(dep) + ")" if dep else ""
        lines.append(str(sid) + ". [" + str(act) + "] " + str(exp) + dep_str)
    return "\n".join(lines)
