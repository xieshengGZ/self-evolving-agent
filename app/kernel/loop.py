"""Agent loop and model protocol helpers.

Orchestration lives here so `app/main.py` can stay a thin bootstrap. Planning
policy comes from the `planning` plugin; `app.planning` owns the typed plan,
state transitions, verification, and persistence.
"""
import json
import re
import time
from typing import Any, Dict, List, Optional

from app import config, context, deepseek, planning, session
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


def _available_actions(runtime: Any) -> List[str]:
    names: List[str] = []
    try:
        names = [str(name) for name in runtime.plugins.tool_names()]
    except Exception:  # noqa: BLE001
        pass
    if "done" not in names:
        names.append("done")
    return sorted(set(names))


def _action_problem(action: Dict[str, Any], runtime: Any) -> Optional[str]:
    name = action.get("action") if isinstance(action, dict) else None
    actions = ", ".join(_available_actions(runtime))
    if not isinstance(name, str) or not name.strip():
        return (
            "模型回复不是可执行动作：缺少非空字符串字段 action。"
            "只返回一个 JSON 动作对象，例如 {\"action\":\"shell\",\"command\":\"...\"} "
            f"或 {{\"action\":\"done\",\"message\":\"...\"}}；可用动作: {actions}。"
        )
    if name not in _available_actions(runtime):
        return f"未知 action: {name}。可用动作: {actions}。请重新选择。"
    return None


def _append_event(kind: str, message: str) -> None:
    session.add_event(kind, message)


def _start_plan(runtime: Any) -> Optional[planning.Plan]:
    if not config.AGENT_ENABLE_PLANNER:
        return None

    plan = planning.load_plan()
    task = (session.get_task() or "").strip()
    if plan is not None and plan.status in ("done", "failed", "cancelled"):
        plan = None

    if plan is not None:
        if task and plan.goal and plan.goal.strip() != task:
            planning.clear_plan("任务目标变化")
            _append_event("info", "任务目标变化，已重置旧计划")
            plan = None
        else:
            _append_event("info", f"恢复计划 #{plan.id} ({plan.status})")
            planning.begin_execution(plan)
            return plan

    if not task:
        return None

    try:
        created = planning.create_plan(task, runtime=runtime)
        _append_event("plan", f"已规划 {len(created.steps)} 步")
        try:
            runtime.emit_hook("plan.created", plan=created.to_dict())
        except Exception:  # noqa: BLE001
            pass
        return created
    except Exception as exc:  # noqa: BLE001
        _append_event("warn", f"规划失败降级：{type(exc).__name__}: {exc}")
        return None


def _replan(plan: planning.Plan, feedback: str, runtime: Any) -> Optional[planning.Plan]:
    if not planning.can_replan(plan):
        _append_event("warn", f"重规划次数已达上限 {config.AGENT_MAX_REPLANS}")
        return None
    try:
        revised = planning.revise_plan(plan, feedback, runtime=runtime)
        _append_event("plan", f"已重规划 revision {revised.revision}：{feedback[:300]}")
        try:
            runtime.emit_hook(
                "plan.revised",
                plan=revised.to_dict(),
                feedback=(feedback or "")[:1000],
            )
        except Exception:  # noqa: BLE001
            pass
        return revised
    except Exception as exc:  # noqa: BLE001
        _append_event("error", f"重规划失败：{type(exc).__name__}: {exc}")
        return None


def agent_loop() -> None:
    """自愈循环：每步 try/except，失败转 error 回传模型，有硬性上限。"""
    runtime = get_runtime()
    steps = 0
    plan = _start_plan(runtime)

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
                revised = None
                if plan is not None:
                    revised = _replan(
                        plan,
                        f"连续失败 {fails} 次，请修正方案。",
                        runtime,
                    )
                if revised is None:
                    _append_event("error", f"连续失败 {fails} 次，停止自愈")
                    break
                plan = revised
                fails = 0
                last_action_str = ""
                repeat_count = 0
                continue
            steps += 1
            _append_event("info", f"--- step {steps}/{config.AGENT_MAX_STEPS} ---")

            if plan is not None:
                try:
                    planning.before_step(plan)
                except Exception as exc:  # noqa: BLE001
                    _append_event("warn", f"更新计划步骤失败: {exc}")

            try:
                system_prompt = load_system_prompt()
            except Exception as exc:  # noqa: BLE001
                system_prompt = FALLBACK_PROMPT
                _append_event("error", f"读 system.md 失败: {exc}")

            try:
                messages = context.build(system_prompt, runtime=runtime)["messages"]
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
                _fail(
                    "无法解析为 JSON 动作，请只返回一个 JSON 对象，不要加解释或围栏。"
                    "可用动作: " + ", ".join(_available_actions(runtime))
                )
                continue
            problem = _action_problem(action, runtime)
            if problem:
                fails += 1
                _fail(problem)
                continue
            fails = 0

            action_str = json.dumps(action, ensure_ascii=False, sort_keys=True)
            if action_str == last_action_str:
                repeat_count += 1
            else:
                last_action_str = action_str
                repeat_count = 0
            if repeat_count >= 3:
                revised = None
                if plan is not None:
                    revised = _replan(
                        plan,
                        "连续 3 次相同动作，计划可能已失效，请换一种做法。",
                        runtime,
                    )
                if revised is None:
                    _append_event("error", "连续 3 次相同动作，强制终止")
                    break
                plan = revised
                last_action_str = ""
                repeat_count = 0
                continue

            name = action.get("action")
            _append_event("action", json.dumps(action, ensure_ascii=False)[:2000])

            if name == "done":
                if plan is None:
                    _append_event("done", str(action.get("message", "")))
                    session.persist()
                    break
                try:
                    plan.mark_remaining_skipped("模型提前结束")
                    planning.start_verification(plan)
                    verification = planning.verify_plan(plan)
                except Exception as exc:  # noqa: BLE001
                    _append_event("error", f"验收异常: {type(exc).__name__}: {exc}")
                    planning.fail(plan, f"验收异常: {exc}")
                    break
                if verification.get("ok"):
                    planning.complete(plan)
                    _append_event("done", str(action.get("message", "")))
                    session.persist()
                    break
                feedback = planning.verification_feedback(verification)
                _append_event("warn", "验收失败：" + feedback)
                revised = _replan(plan, "验收失败：\n" + feedback, runtime)
                if revised is None:
                    planning.fail(plan, "验收失败且无法重规划")
                    _append_event("error", "验收失败且无法重规划")
                    break
                plan = revised
                fails = 0
                last_action_str = ""
                repeat_count = 0
                continue

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
            except Exception as exc:  # noqa: BLE001
                fails += 1
                result = {"error": f"{type(exc).__name__}: {exc}"}
                _append_event(
                    "error",
                    f"execute_tool 失败({fails}/{config.AGENT_MAX_CONSECUTIVE_FAILURES}): "
                    f"{result['error']}",
                )
            else:
                if isinstance(result, dict) and result.get("error"):
                    fails += 1
                else:
                    fails = 0

            if plan is not None:
                try:
                    planning.record_result(plan, action, result)
                except Exception as exc:  # noqa: BLE001
                    _append_event("warn", f"记录计划步骤失败: {exc}")

            try:
                summary, _artifact = context.offload_result(action, result)
                context_result = json.dumps(summary, ensure_ascii=False)
                session.append_history("user", context_result)
                try:
                    session.record_step(action, result)
                except Exception:  # noqa: BLE001
                    pass
                _append_event("result", context_result[:2000])
                session.persist()
            except Exception as exc:  # noqa: BLE001
                _append_event("error", f"写结果失败: {exc}")

            if session.is_cancelled():
                _append_event("info", "任务被用户取消")
                break

        if steps >= config.AGENT_MAX_STEPS:
            _append_event("info", f"达到最大步数 {config.AGENT_MAX_STEPS}，计划保留，可继续")
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
        if plan is not None:
            try:
                planning.fail(plan, f"agent_loop 致命异常: {exc}")
            except Exception:  # noqa: BLE001
                pass
    finally:
        try:
            session.set_status("idle")
            session.clear_cancel()
            session.persist()
        except Exception:  # noqa: BLE001
            pass
