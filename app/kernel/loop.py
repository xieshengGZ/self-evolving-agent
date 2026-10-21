"""Agent loop and model protocol helpers.

Orchestration lives here so `app/main.py` can stay a thin bootstrap. Planning
and context assembly are still inline; Phase 2/3 will move them behind the
planner/context registries.
"""
import json
import re
import time
from typing import Any, Dict, List, Optional

from app import config, deepseek, planner, session
from app.kernel import get_runtime

FALLBACK_PROMPT = (
    "你是一个可以操作 Linux 系统的 AI Agent。每次只返回一个 JSON 动作，"
    "不要输出多余文字。可用动作由已加载插件提供。"
    "完成后返回 {\"action\":\"done\",\"message\":\"...\"}。"
    "不得输出任何密钥或令牌。小步执行并验证结果。"
)


def load_system_prompt() -> str:
    try:
        with open(config.SYSTEM_PROMPT_PATH, "r", encoding="utf-8") as handle:
            content = handle.read().strip()
        if content:
            return content
    except FileNotFoundError:
        pass
    except Exception:  # noqa: BLE001
        pass
    return FALLBACK_PROMPT


def parse_action(text: Any) -> Optional[Dict[str, Any]]:
    """兼容 str 和 dict：DeepSeek 客户端若返回已解析的 dict，直接使用。"""
    if isinstance(text, dict):
        return text
    if not isinstance(text, str):
        return None
    try:
        text = text.strip()
    except Exception:  # noqa: BLE001
        return None
    if not text:
        return None
    if text.startswith("```"):
        first_nl = text.find("\n")
        if first_nl != -1:
            text = text[first_nl + 1:]
            if text.rstrip().endswith("```"):
                text = text.rstrip()[:-3]
    text = re.sub(
        r"\s*</(?:parameter|response|output|content|answer)>\s*$", "", text, flags=re.I
    )
    text = text.strip()
    if not text:
        return None
    try:
        obj = json.loads(text)
    except Exception:  # noqa: BLE001
        return None
    return obj if isinstance(obj, dict) else None


def _append_event(kind: str, message: str) -> None:
    session.add_event(kind, message)


def agent_loop() -> None:
    """自愈循环：每步 try/except，失败转 error 回传模型，有硬性上限。"""
    runtime = get_runtime()
    steps = 0

    if config.AGENT_ENABLE_PLANNER:
        try:
            state = session.get_task_state() or {}
        except Exception:  # noqa: BLE001
            state = {}
        if not state.get("plan"):
            task = session.get_task()
            if task and task.strip():
                try:
                    plan_result = planner.plan(task)
                    if "error" in plan_result:
                        _append_event("warn", "规划失败降级：" + plan_result["error"])
                    else:
                        state["plan"] = plan_result["steps"]
                        state["plan_text"] = planner.format_plan_text(plan_result["steps"])
                        state["current"] = 0
                        state["task"] = task
                        session.set_task_state(state)
                        _append_event("plan", "已规划 " + str(len(plan_result["steps"])) + " 步")
                except Exception as exc:  # noqa: BLE001
                    _append_event("warn", "规划异常降级：" + str(exc))

    fails = 0
    started = time.time()
    last_action_str = ""
    repeat_count = 0

    def _fail(msg: str) -> None:
        try:
            session.append_history("user", json.dumps({"error": msg}, ensure_ascii=False))
        except Exception:  # noqa: BLE001
            pass
        _append_event("error", msg)
        try:
            session.persist()
        except Exception:  # noqa: BLE001
            pass

    try:
        while steps < config.AGENT_MAX_STEPS:
            if session.is_cancelled():
                _append_event("info", "任务被用户取消")
                break
            if time.time() - started > config.AGENT_MAX_RUNTIME:
                _append_event("error", f"超过最大运行时长 {config.AGENT_MAX_RUNTIME}s")
                break
            if fails >= config.AGENT_MAX_CONSECUTIVE_FAILURES:
                _append_event("error", f"连续失败 {fails} 次，停止自愈")
                break
            steps += 1
            _append_event("info", f"--- step {steps}/{config.AGENT_MAX_STEPS} ---")

            try:
                system_prompt = load_system_prompt()
            except Exception as exc:  # noqa: BLE001
                system_prompt = FALLBACK_PROMPT
                _append_event("error", f"读 system.md 失败: {exc}")

            try:
                messages = session.build_messages(system_prompt)
            except Exception as exc:  # noqa: BLE001
                _append_event("error", f"组装 messages 失败: {exc}")
                break

            try:
                reply = deepseek.chat(messages)
            except Exception as exc:  # noqa: BLE001
                fails += 1
                _fail(
                    f"DeepSeek 调用失败({fails}/{config.AGENT_MAX_CONSECUTIVE_FAILURES}): "
                    f"{type(exc).__name__}: {exc}"
                )
                continue

            try:
                session.append_history("assistant", reply)
                _append_event("assistant", reply)
                session.persist()
            except Exception as exc:  # noqa: BLE001
                _append_event("error", f"写 history 失败: {exc}")

            try:
                action = parse_action(reply)
            except Exception as exc:  # noqa: BLE001
                fails += 1
                _fail(f"parse_action 异常: {type(exc).__name__}: {exc}")
                continue
            if action is None:
                fails += 1
                _fail("无法解析为 JSON 动作，请只返回一个 JSON 对象，不要加解释或围栏")
                continue
            fails = 0

            action_str = json.dumps(action, ensure_ascii=False, sort_keys=True)
            if action_str == last_action_str:
                repeat_count += 1
            else:
                last_action_str = action_str
                repeat_count = 0
            if repeat_count >= 3:
                _append_event("error", "连续 3 次相同动作，强制终止")
                break

            name = action.get("action")
            _append_event("action", json.dumps(action, ensure_ascii=False)[:2000])

            if name == "done":
                _append_event("done", str(action.get("message", "")))
                session.persist()
                break

            if name == "self_restart":
                try:
                    from app._robust import check_restart_allowed

                    ok, reason = check_restart_allowed()
                except Exception:  # noqa: BLE001
                    ok, reason = True, ""
                if not ok:
                    _fail(f"self_restart 被拒绝: {reason}")
                    continue

            try:
                result = runtime.execute_tool(action)
                fails = 0
            except Exception as exc:  # noqa: BLE001
                fails += 1
                result = {"error": f"{type(exc).__name__}: {exc}"}
                _append_event(
                    "error",
                    f"execute_tool 失败({fails}/{config.AGENT_MAX_CONSECUTIVE_FAILURES}): "
                    f"{result['error']}",
                )

            try:
                session.append_history("user", json.dumps(result, ensure_ascii=False))
                try:
                    session.record_step(action, result)
                except Exception:  # noqa: BLE001
                    pass
                _append_event("result", json.dumps(result, ensure_ascii=False)[:2000])
                session.persist()
            except Exception as exc:  # noqa: BLE001
                _append_event("error", f"写结果失败: {exc}")

            if session.is_cancelled():
                _append_event("info", "任务被用户取消")
                break

        if steps >= config.AGENT_MAX_STEPS:
            _append_event("info", f"达到最大步数 {config.AGENT_MAX_STEPS}")
    except Exception as exc:  # noqa: BLE001
        import traceback

        _append_event("error", f"agent_loop 致命异常: {type(exc).__name__}: {exc}")
        try:
            session.append_history(
                "user",
                json.dumps(
                    {
                        "error": f"agent_loop 致命异常: {exc}",
                        "trace": traceback.format_exc()[-2000:],
                    },
                    ensure_ascii=False,
                ),
            )
        except Exception:  # noqa: BLE001
            pass
    finally:
        try:
            session.set_status("idle")
            session.clear_cancel()
            session.persist()
        except Exception:  # noqa: BLE001
            pass
