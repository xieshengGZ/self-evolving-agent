"""Task planning domain: models, state machine, verification, persistence.

The LLM-facing planner lives in the `planning` plugin. This module owns the
stable pieces the agent loop needs: a typed plan, legal state transitions,
deterministic acceptance checks, and SQLite/session persistence.
"""
from __future__ import annotations

import json
import os
import subprocess
import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from app import actions, config, session
from app import storage as storage_mod

PLAN_KEY = "plan"
PLAN_TEXT_KEY = "plan_text"
MAX_ACCEPTANCE_CHECKS = 10

PLAN_STATUSES = {
    "planning",
    "executing",
    "verifying",
    "replan",
    "done",
    "failed",
    "cancelled",
}
STEP_STATUSES = {"pending", "in_progress", "done", "failed", "skipped"}

_ALLOWED_TRANSITIONS: Dict[str, set[str]] = {
    "planning": {"executing", "failed", "cancelled"},
    "executing": {"verifying", "replan", "failed", "cancelled"},
    "verifying": {"done", "replan", "executing", "failed", "cancelled"},
    "replan": {"executing", "failed", "cancelled"},
    "done": set(),
    "failed": set(),
    "cancelled": set(),
}


class PlanningError(RuntimeError):
    pass


def _checks(value: Any) -> List[Dict[str, Any]]:
    if isinstance(value, dict):
        return [dict(value)]
    if isinstance(value, list):
        return [dict(item) for item in value if isinstance(item, dict)]
    return []


def _ids(value: Any) -> List[int]:
    if not isinstance(value, (list, tuple)):
        return []
    result: List[int] = []
    for item in value:
        try:
            result.append(int(item))
        except (TypeError, ValueError):
            continue
    return result


@dataclass
class PlanStep:
    id: int
    action: str = ""
    args: Dict[str, Any] = field(default_factory=dict)
    expected: str = ""
    acceptance: List[Dict[str, Any]] = field(default_factory=list)
    depends_on: List[int] = field(default_factory=list)
    status: str = "pending"
    evidence: Dict[str, Any] = field(default_factory=dict)
    attempts: int = 0

    def __post_init__(self) -> None:
        try:
            self.id = int(self.id)
        except (TypeError, ValueError):
            self.id = 1
        self.action = str(self.action or "").strip()
        if not isinstance(self.args, dict):
            self.args = {}
        self.expected = str(self.expected or "")
        self.acceptance = _checks(self.acceptance)
        self.depends_on = _ids(self.depends_on)
        if self.status not in STEP_STATUSES:
            self.status = "pending"
        if not isinstance(self.evidence, dict):
            self.evidence = {}
        try:
            self.attempts = max(0, int(self.attempts))
        except (TypeError, ValueError):
            self.attempts = 0

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "action": self.action,
            "args": dict(self.args),
            "expected": self.expected,
            "acceptance": list(self.acceptance),
            "depends_on": list(self.depends_on),
            "status": self.status,
            "evidence": dict(self.evidence),
            "attempts": self.attempts,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any], fallback_id: int = 1) -> "PlanStep":
        if not isinstance(data, dict):
            data = {"expected": str(data)}
        return cls(
            id=data.get("id", fallback_id),
            action=data.get("action", ""),
            args=data.get("args") if isinstance(data.get("args"), dict) else {},
            expected=data.get("expected", data.get("text", "")),
            acceptance=data.get("acceptance", []),
            depends_on=data.get("depends_on", []),
            status=data.get("status", "pending"),
            evidence=data.get("evidence", {}) if isinstance(data.get("evidence"), dict) else {},
            attempts=data.get("attempts", 0),
        )


@dataclass
class Plan:
    goal: str
    steps: List[PlanStep]
    status: str = "planning"
    acceptance: List[Dict[str, Any]] = field(default_factory=list)
    reasoning: str = ""
    current: int = 0
    revision: int = 0
    id: int = 0
    conv_id: str = ""
    created_at: float = 0.0
    updated_at: float = 0.0
    feedback: str = ""
    verification: Dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        self.goal = str(self.goal or "")
        self.steps = [
            step if isinstance(step, PlanStep) else PlanStep.from_dict(step, index)
            for index, step in enumerate(self.steps or [], 1)
        ]
        seen: set[int] = set()
        for index, step in enumerate(self.steps, 1):
            if step.id in seen:
                step.id = index
            seen.add(step.id)
        if self.status not in PLAN_STATUSES:
            self.status = "executing"
        self.acceptance = _checks(self.acceptance)
        self.reasoning = str(self.reasoning or "")[:2000]
        self.feedback = str(self.feedback or "")[:2000]
        if not isinstance(self.verification, dict):
            self.verification = {}
        try:
            self.current = max(0, min(int(self.current), len(self.steps)))
        except (TypeError, ValueError):
            self.current = 0
        try:
            self.revision = max(0, int(self.revision))
        except (TypeError, ValueError):
            self.revision = 0

    # ---------------- state ----------------
    def transition(self, status: str) -> bool:
        if status == self.status:
            return True
        if status not in PLAN_STATUSES:
            return False
        if status not in _ALLOWED_TRANSITIONS.get(self.status, set()):
            return False
        self.status = status
        self.updated_at = time.time()
        return True

    def _open_index(self, start: int = 0) -> int:
        for index in range(max(0, start), len(self.steps)):
            if self.steps[index].status in ("pending", "in_progress", "failed"):
                return index
        return -1

    def current_step(self) -> Optional[PlanStep]:
        if not self.steps:
            return None
        if 0 <= self.current < len(self.steps):
            step = self.steps[self.current]
            if step.status not in ("done", "skipped"):
                return step
        index = self._open_index(0)
        self.current = index if index >= 0 else len(self.steps)
        return self.steps[self.current] if index >= 0 else None

    def pending_count(self) -> int:
        return sum(1 for step in self.steps if step.status in ("pending", "in_progress", "failed"))

    def mark_remaining_skipped(self, reason: str = "") -> None:
        for step in self.steps:
            if step.status in ("pending", "in_progress", "failed"):
                step.status = "skipped"
                if reason:
                    step.evidence["skipped"] = reason
        self.current = len(self.steps)

    def format_text(self) -> str:
        symbols = {
            "pending": " ",
            "in_progress": ">",
            "done": "x",
            "failed": "!",
            "skipped": "-",
        }
        lines = [
            "目标: " + (self.goal or "(未命名)"),
            f"状态: {self.status} · revision {self.revision} · "
            f"{sum(1 for s in self.steps if s.status == 'done')}/{len(self.steps)} 步完成",
        ]
        for step in self.steps:
            mark = symbols.get(step.status, " ")
            detail = step.expected or step.action or ""
            lines.append(f"{step.id}. [{mark}] {step.action}: {detail}")
        if self.acceptance:
            lines.append("验收: " + json.dumps(self.acceptance, ensure_ascii=False))
        return "\n".join(lines)

    # ---------------- serialization ----------------
    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "conv_id": self.conv_id,
            "goal": self.goal,
            "status": self.status,
            "acceptance": list(self.acceptance),
            "reasoning": self.reasoning,
            "steps": [step.to_dict() for step in self.steps],
            "current": self.current,
            "revision": self.revision,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "feedback": self.feedback,
            "verification": dict(self.verification),
        }

    @classmethod
    def from_dict(
        cls,
        data: Any,
        *,
        conv_id: str = "",
        goal: str = "",
    ) -> "Plan":
        if isinstance(data, list):
            return cls(
                goal=goal,
                steps=[PlanStep.from_dict(item, index) for index, item in enumerate(data, 1)],
                status="executing",
                conv_id=conv_id,
            )
        if not isinstance(data, dict):
            raise PlanningError("plan must be an object")
        raw_steps = data.get("steps")
        if raw_steps is None:
            raw_steps = data.get("plan") or []
        if not isinstance(raw_steps, list) or not raw_steps:
            raise PlanningError("plan has no steps")
        plan = cls(
            goal=data.get("goal") or goal,
            steps=[PlanStep.from_dict(item, index) for index, item in enumerate(raw_steps, 1)],
            status=data.get("status") or "executing",
            acceptance=data.get("acceptance", []),
            reasoning=data.get("reasoning", ""),
            current=data.get("current", 0),
            revision=data.get("revision", 0),
            id=data.get("id", 0) or 0,
            conv_id=data.get("conv_id") or conv_id,
            created_at=float(data.get("created_at") or 0),
            updated_at=float(data.get("updated_at") or 0),
            feedback=data.get("feedback", ""),
            verification=data.get("verification", {}),
        )
        if plan.status == "active":
            plan.status = "executing"
        return plan

    @classmethod
    def from_storage(cls, row: Dict[str, Any]) -> "Plan":
        plan = cls.from_dict(
            {
                "id": row.get("id", 0),
                "conv_id": row.get("conv_id", ""),
                "goal": row.get("goal", ""),
                "status": row.get("status", "executing"),
                "steps": row.get("steps", []),
                "acceptance": row.get("acceptance", []),
                "current": row.get("current_step", 0),
                "revision": row.get("revision", 0),
                "created_at": row.get("created_at", 0),
                "updated_at": row.get("updated_at", 0),
            },
            conv_id=str(row.get("conv_id") or ""),
            goal=str(row.get("goal") or ""),
        )
        return plan


# ---------------------------------------------------------------------------
# persistence
# ---------------------------------------------------------------------------
def _audit_storage() -> storage_mod.Storage:
    return storage_mod.Storage()


def save(plan: Plan, *, audit: bool = True) -> Plan:
    """Persist to the live session first, then to the plans audit table."""
    if not plan.conv_id:
        plan.conv_id = session.get_conv_id()
    plan.updated_at = time.time()
    if plan.created_at <= 0:
        plan.created_at = plan.updated_at

    if audit and plan.conv_id:
        try:
            storage = _audit_storage()
            if plan.id:
                storage.update_plan(
                    plan.id,
                    goal=plan.goal,
                    status=plan.status,
                    steps=[step.to_dict() for step in plan.steps],
                    acceptance=plan.acceptance,
                    current_step=plan.current,
                    revision=plan.revision,
                )
            else:
                plan.id = storage.save_plan(
                    plan.conv_id,
                    plan.goal,
                    [step.to_dict() for step in plan.steps],
                    plan.status,
                    acceptance=plan.acceptance,
                    current_step=plan.current,
                    revision=plan.revision,
                )
        except Exception:  # noqa: BLE001
            pass

    state = dict(session.get_task_state() or {})
    state[PLAN_KEY] = plan.to_dict()
    state[PLAN_TEXT_KEY] = plan.format_text()
    state["plan_status"] = plan.status
    state["plan_goal"] = plan.goal
    state["current"] = plan.current
    session.set_task_state(state)
    return plan


def load_plan() -> Optional[Plan]:
    state = session.get_task_state() or {}
    raw = state.get(PLAN_KEY)
    if raw:
        try:
            return Plan.from_dict(
                raw,
                conv_id=session.get_conv_id(),
                goal=str(state.get("plan_goal") or session.get_task() or ""),
            )
        except Exception:  # noqa: BLE001
            pass
    conv_id = session.get_conv_id()
    if conv_id:
        try:
            rows = _audit_storage().list_plans(conv_id, limit=20)
        except Exception:  # noqa: BLE001
            rows = []
        for row in rows:
            if str(row.get("status") or "") in ("done", "failed", "cancelled"):
                continue
            try:
                plan = Plan.from_storage(row)
                save(plan, audit=False)
                return plan
            except Exception:  # noqa: BLE001
                continue
    return None


def has_plan() -> bool:
    return load_plan() is not None


def clear_plan(reason: str = "") -> Optional[Plan]:
    plan = load_plan()
    if plan is not None and plan.status not in ("done", "failed", "cancelled"):
        try:
            if plan.id:
                _audit_storage().update_plan(
                    plan.id,
                    status="cancelled",
                    revision=plan.revision,
                )
            elif plan.conv_id:
                rows = _audit_storage().list_plans(plan.conv_id, limit=20)
                for row in rows:
                    if str(row.get("status") or "") not in ("done", "failed", "cancelled"):
                        _audit_storage().update_plan(int(row["id"]), status="cancelled")
        except Exception:  # noqa: BLE001
            pass
    state = dict(session.get_task_state() or {})
    for key in (
        PLAN_KEY,
        PLAN_TEXT_KEY,
        "plan_status",
        "plan_goal",
        "current",
    ):
        state.pop(key, None)
    if reason:
        state["plan_reset_reason"] = reason
        state["plan_reset_at"] = time.time()
    session.set_task_state(state)
    return plan


# ---------------------------------------------------------------------------
# planner bridge
# ---------------------------------------------------------------------------
def _get_planner(runtime: Any = None) -> Any:
    if runtime is None:
        return None
    try:
        return runtime.plugins.get_planner("default")
    except Exception:  # noqa: BLE001
        return None


def _tool_names(runtime: Any = None) -> List[str]:
    if runtime is None:
        return []
    try:
        return list(runtime.plugins.tool_names())
    except Exception:  # noqa: BLE001
        return []


def _plan_from_result(raw: Any, task: str, revision: int = 0) -> Plan:
    if isinstance(raw, dict) and raw.get("error"):
        raise PlanningError(str(raw["error"]))
    plan = Plan.from_dict(raw, goal=task)
    if not plan.steps:
        raise PlanningError("planner returned an empty plan")
    limit = max(1, int(getattr(config, "AGENT_PLAN_MAX_STEPS", 8)))
    if len(plan.steps) > limit:
        plan.steps = plan.steps[:limit]
    plan.revision = revision
    plan.created_at = time.time()
    plan.updated_at = plan.created_at
    plan.conv_id = session.get_conv_id()
    if plan.status in ("planning", "replan", "active"):
        plan.status = "planning"
        plan.transition("executing")
    return plan


def create_plan(task: str, *, runtime: Any = None) -> Plan:
    task = (task or "").strip()
    if not task:
        raise PlanningError("empty task")
    planner = _get_planner(runtime)
    if planner is not None and callable(getattr(planner, "create", None)):
        raw = planner.create(task, actions=_tool_names(runtime))
    else:
        from app import planner as legacy_planner

        raw = legacy_planner.plan(task)
    plan = _plan_from_result(raw, task)
    save(plan)
    return plan


def can_replan(plan: Optional[Plan]) -> bool:
    if plan is None:
        return False
    return plan.revision < max(0, int(getattr(config, "AGENT_MAX_REPLANS", 2)))


def revise_plan(plan: Plan, feedback: str, *, runtime: Any = None) -> Plan:
    if not can_replan(plan):
        raise PlanningError("replan limit reached")
    planner = _get_planner(runtime)
    raw: Any = None
    if planner is not None and callable(getattr(planner, "revise", None)):
        raw = planner.revise(plan.to_dict(), feedback, actions=_tool_names(runtime))
    else:
        from app import planner as legacy_planner

        raw = legacy_planner.plan(
            plan.goal + "\n\n上次执行失败/验收反馈：\n" + (feedback or "")[:2000]
        )
    revised = _plan_from_result(raw, plan.goal, revision=plan.revision + 1)
    revised.conv_id = plan.conv_id or session.get_conv_id()
    revised.feedback = (feedback or "")[:2000]

    plan.feedback = (feedback or "")[:2000]
    if not plan.transition("replan"):
        plan.status = "replan"
    save(plan)
    save(revised)
    return revised


# ---------------------------------------------------------------------------
# execution helpers
# ---------------------------------------------------------------------------
def begin_execution(plan: Plan) -> Plan:
    if plan.status != "executing":
        plan.transition("executing")
    plan.current_step()
    return save(plan)


def before_step(plan: Plan) -> Optional[PlanStep]:
    step = plan.current_step()
    if step is not None and step.status == "pending":
        step.status = "in_progress"
        save(plan)
    return step


def _result_summary(result: Any) -> Dict[str, Any]:
    if not isinstance(result, dict):
        return {"result": str(result)[:1000]}
    keys = ("exit_code", "timed_out", "error", "path", "bytes", "result_of")
    summary = {key: result[key] for key in keys if key in result}
    for key in ("stdout", "stderr", "output"):
        value = result.get(key)
        if isinstance(value, str):
            summary[key] = value[-1200:]
    if not summary:
        try:
            summary["result"] = json.dumps(result, ensure_ascii=False)[:1200]
        except Exception:  # noqa: BLE001
            summary["result"] = str(result)[:1200]
    return summary


def record_result(plan: Plan, action: Dict[str, Any], result: Any) -> str:
    if not plan.steps:
        return "ignored"
    step = plan.current_step()
    if step is None:
        return "ignored"
    name = str((action or {}).get("action") or "")
    if step.action and step.action not in ("*", "any") and name != step.action:
        return "ignored"

    step.attempts += 1
    step.evidence["attempts"] = step.attempts
    step.evidence["last"] = _result_summary(result)
    failed = False
    if isinstance(result, dict):
        failed = bool(result.get("error")) or bool(result.get("timed_out"))
        exit_code = result.get("exit_code")
        if isinstance(exit_code, int) and exit_code != 0:
            failed = True
    if failed:
        step.status = "failed"
        save(plan)
        return "failed"

    step.status = "done"
    index = plan.steps.index(step)
    next_index = plan._open_index(index + 1)
    plan.current = next_index if next_index >= 0 else len(plan.steps)
    save(plan)
    return "done"


def start_verification(plan: Plan) -> Plan:
    if plan.status != "verifying":
        plan.transition("verifying")
    return save(plan)


def complete(plan: Plan) -> Plan:
    plan.transition("done")
    return save(plan)


def fail(plan: Plan, reason: str) -> Plan:
    plan.feedback = (reason or "")[:2000]
    plan.transition("failed")
    return save(plan)


# ---------------------------------------------------------------------------
# deterministic verification
# ---------------------------------------------------------------------------
def _abs_path(path: str) -> str:
    path = os.path.expanduser(str(path or "").strip())
    if not os.path.isabs(path):
        path = os.path.join(config.AGENT_ROOT, path)
    return os.path.abspath(path)


def _run_command(command: str, timeout: int) -> Dict[str, Any]:
    danger = actions.is_dangerous_command(command)
    if danger:
        return {"ok": False, "error": f"命令被拒绝执行: {danger}"}
    try:
        proc = subprocess.run(
            command,
            shell=True,
            cwd=config.AGENT_ROOT,
            capture_output=True,
            text=True,
            errors="replace",
            timeout=max(1, timeout),
        )
    except subprocess.TimeoutExpired as exc:
        return {
            "ok": False,
            "error": f"命令超时 {timeout}s",
            "stdout": (exc.stdout or "")[-1000:] if isinstance(exc.stdout, str) else "",
            "stderr": (exc.stderr or "")[-1000:] if isinstance(exc.stderr, str) else "",
        }
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "error": f"{type(exc).__name__}: {exc}"}
    return {
        "ok": True,
        "exit_code": proc.returncode,
        "stdout": (proc.stdout or "")[-1000:],
        "stderr": (proc.stderr or "")[-1000:],
        "_returncode": proc.returncode,
    }


def _run_check(check: Dict[str, Any], plan: Plan) -> Dict[str, Any]:
    kind = str(check.get("kind") or check.get("type") or "").strip().lower()
    if not kind:
        return {"ok": False, "error": "acceptance check missing kind", "check": check}

    if kind == "manual":
        return {
            "ok": True,
            "skipped": True,
            "text": str(check.get("text") or check.get("description") or ""),
        }

    if kind in ("file_exists", "exists"):
        path = _abs_path(check.get("path", ""))
        actual = os.path.exists(path)
        expected = bool(check.get("exists", True))
        return {
            "ok": actual == expected,
            "kind": kind,
            "path": path,
            "exists": actual,
            "expected": expected,
        }

    if kind in ("file_not_empty", "not_empty"):
        path = _abs_path(check.get("path", ""))
        min_bytes = int(check.get("min_bytes", 1) or 1)
        try:
            size = os.path.getsize(path) if os.path.isfile(path) else 0
        except OSError:
            size = 0
        return {
            "ok": os.path.isfile(path) and size >= min_bytes,
            "kind": kind,
            "path": path,
            "size": size,
            "min_bytes": min_bytes,
        }

    if kind == "contains":
        path = _abs_path(check.get("path", ""))
        needle = str(check.get("text") or check.get("value") or "")
        try:
            with open(path, "r", encoding="utf-8", errors="replace") as handle:
                content = handle.read()
        except Exception as exc:  # noqa: BLE001
            return {"ok": False, "kind": kind, "path": path, "error": str(exc)}
        return {
            "ok": bool(needle) and needle in content,
            "kind": kind,
            "path": path,
            "text": needle,
        }

    command = str(check.get("command") or check.get("test") or "").strip()
    if kind in ("command", "test"):
        if not command:
            return {"ok": False, "kind": kind, "error": "command is required"}
        timeout = int(check.get("timeout") or min(config.AGENT_CMD_TIMEOUT, 120))
        result = _run_command(command, timeout)
        expected = int(check.get("expected_code", check.get("value", 0)) or 0)
        result.update({"kind": kind, "command": command, "expected_code": expected})
        if result.pop("_returncode", None) is not None:
            result["ok"] = result.get("exit_code") == expected
        return result

    if kind == "exit_code":
        expected = int(check.get("expected_code", check.get("value", 0)) or 0)
        if command:
            timeout = int(check.get("timeout") or min(config.AGENT_CMD_TIMEOUT, 120))
            result = _run_command(command, timeout)
            result.update({"kind": kind, "command": command, "expected_code": expected})
            result.pop("_returncode", None)
            result["ok"] = result.get("exit_code") == expected
            return result
        last_evidence = plan.steps[-1].evidence.get("last", {}) if plan.steps else {}
        actual = last_evidence.get("exit_code")
        return {
            "ok": actual == expected,
            "kind": kind,
            "exit_code": actual,
            "expected_code": expected,
        }

    return {"ok": False, "kind": kind, "error": f"unknown acceptance kind: {kind}"}


def verify_plan(plan: Plan) -> Dict[str, Any]:
    checks: List[Dict[str, Any]] = list(plan.acceptance)
    if not checks:
        for step in plan.steps:
            checks.extend(step.acceptance)
    checks = checks[:MAX_ACCEPTANCE_CHECKS]
    results = [_run_check(check, plan) for check in checks]
    result = {
        "ok": all(item.get("ok") for item in results) if results else True,
        "checks": results,
        "pending_steps": [
            step.id for step in plan.steps if step.status in ("pending", "in_progress", "failed")
        ],
        "truncated": len(checks) == MAX_ACCEPTANCE_CHECKS and len(checks) < len(plan.acceptance),
    }
    plan.verification = result
    save(plan)
    return result


def verification_feedback(result: Dict[str, Any]) -> str:
    lines: List[str] = []
    for item in result.get("checks", []):
        if item.get("ok"):
            continue
        kind = item.get("kind", "check")
        detail = item.get("error") or item.get("path") or item.get("command") or item.get("text")
        lines.append(f"- {kind}: {detail}")
    pending = result.get("pending_steps") or []
    if pending:
        lines.append("- 未完成步骤: " + ", ".join(str(item) for item in pending))
    return "\n".join(lines)[:2000] or "验收未通过"


def snapshot() -> Optional[Dict[str, Any]]:
    plan = load_plan()
    return plan.to_dict() if plan is not None else None
