import json
import os
import threading
import time
from typing import Any, Dict, List, Optional

from app import config
from app import memory

STATE_DIR = os.path.join(config.AGENT_ROOT, "state")
SESSION_FILE = os.path.join(STATE_DIR, "session.json")
ARCHIVE_DIR = os.path.join(STATE_DIR, "archive")

# 工作窗口：只保留最近 N 条动作+结果，不再全量持久化
WINDOW_SIZE = int(os.environ.get("AGENT_WINDOW_SIZE", "8"))
# 单条结果进窗口前的截断长度
RESULT_MAX_CHARS = int(os.environ.get("AGENT_RESULT_MAX_CHARS", "1500"))


class Session:
    def __init__(self) -> None:
        self.history: List[Dict[str, str]] = []  # 工作窗口
        self.events: List[Dict[str, Any]] = []
        self.status: str = "idle"
        self.cancel_flag: bool = False
        self.current_proc = None
        self.conv_id: str = ""
        self.task: str = ""
        self.task_state: Dict[str, Any] = {}
        self.lock = threading.RLock()

    # ---------------- events ----------------
    def add_event(self, kind: str, message: str, **extra: Any) -> None:
        with self.lock:
            if not isinstance(message, str):
                message = json.dumps(message, ensure_ascii=False)
            event: Dict[str, Any] = {
                "ts": time.time(),
                "kind": kind,
                "message": message,
            }
            if extra:
                event.update(extra)
            self.events.append(event)
            # events 只保留最近 500 条，避免无限增长
            if len(self.events) > 500:
                self.events = self.events[-500:]

    # ---------------- window ----------------
    def add_message(self, role: str, content: Any) -> None:
        if not isinstance(content, str):
            content = json.dumps(content, ensure_ascii=False)
        if len(content) > RESULT_MAX_CHARS:
            content = content[:RESULT_MAX_CHARS] + "…（已截断）"
        with self.lock:
            self.history.append({"role": role, "content": content})
            if len(self.history) > WINDOW_SIZE:
                self.history = self.history[-WINDOW_SIZE:]

    def clear_window(self) -> None:
        with self.lock:
            self.history = []

    def set_task(self, task: str) -> None:
        with self.lock:
            self.task = task or ""

    def new_conversation(self, task: str) -> str:
        """归档旧窗口，开新对话，返回新 conv_id。"""
        with self.lock:
            old = list(self.history)
            old_conv = self.conv_id
            self.history = []
            self.task = task or ""
            self.task_state = {}
            self.conv_id = memory.new_conv_id()
        if old:
            try:
                os.makedirs(ARCHIVE_DIR, exist_ok=True)
                ts = str(int(time.time()))
                path = os.path.join(ARCHIVE_DIR, (old_conv or "unknown") + "-" + ts + ".json")
                with open(path, "w", encoding="utf-8") as f:
                    json.dump({"conv_id": old_conv, "history": old}, f, ensure_ascii=False, indent=2)
            except Exception:
                pass
        try:
            memory.ensure(self.conv_id, title=(task or "")[:120])
        except Exception:
            pass
        return self.conv_id

    # ---------------- messages ----------------
    def build_messages(self, system_prompt: str) -> List[Dict[str, str]]:
        """system + 提炼记忆 + 当前任务 + 最近窗口。"""
        messages: List[Dict[str, str]] = [{"role": "system", "content": system_prompt}]
        mem_text = ""
        try:
            if self.conv_id:
                mem_text = memory.read_text(self.conv_id, max_bytes=6000)
        except Exception:
            mem_text = ""
        if mem_text.strip():
            messages.append({
                "role": "system",
                "content": "## 长期记忆（提炼知识，来自 state/memory/" + self.conv_id + ".md）\n" + mem_text,
            })
        if self.task:
            messages.append({"role": "user", "content": "当前任务：" + self.task})
        with self.lock:
            _ts = dict(self.task_state) if self.task_state else {}
        if _ts.get("plan_text"):
            messages.append({"role": "system", "content": "## 执行计划\n" + _ts["plan_text"]})
        with self.lock:
            messages.extend(list(self.history))
        return messages

    # ---------------- status ----------------
    def set_status(self, status: str) -> None:
        with self.lock:
            self.status = status

    def request_cancel(self) -> None:
        with self.lock:
            self.cancel_flag = True
        proc = self.current_proc
        if proc is not None:
            try:
                import signal
                os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
            except Exception:
                try:
                    proc.kill()
                except Exception:
                    pass

    def is_cancelled(self) -> bool:
        with self.lock:
            return self.cancel_flag

    def reset_cancel(self) -> None:
        with self.lock:
            self.cancel_flag = False

    def snapshot(self, since: int = 0) -> Dict[str, Any]:
        with self.lock:
            events = self.events[since:] if since >= 0 else self.events
            return {
                "status": self.status,
                "history": list(self.history),
                "events": list(events),
                "event_count": len(self.events),
                "conv_id": self.conv_id,
                "task": self.task,
            }

    def to_dict(self) -> Dict[str, Any]:
        with self.lock:
            return {
                "history": list(self.history),
                "events": list(self.events),
                "status": self.status,
                "conv_id": self.conv_id,
                "task": self.task,
                "task_state": dict(self.task_state),
            }

    def persist(self) -> None:
        try:
            os.makedirs(STATE_DIR, exist_ok=True)
            data = self.to_dict()
            tmp = SESSION_FILE + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
            os.replace(tmp, SESSION_FILE)
        except Exception:
            pass

    def load(self) -> None:
        if not os.path.exists(SESSION_FILE):
            return
        try:
            with open(SESSION_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
        except Exception:
            return
        with self.lock:
            hist = data.get("history", []) or []
            # 迁移：旧版本可能存了全量 history，这里只保留最近窗口
            if len(hist) > WINDOW_SIZE:
                try:
                    os.makedirs(ARCHIVE_DIR, exist_ok=True)
                    ts = str(int(time.time()))
                    path = os.path.join(ARCHIVE_DIR, "legacy-" + ts + ".json")
                    with open(path, "w", encoding="utf-8") as f:
                        json.dump({"history": hist}, f, ensure_ascii=False, indent=2)
                except Exception:
                    pass
                hist = hist[-WINDOW_SIZE:]
            self.history = hist
            self.events = (data.get("events", []) or [])[-500:]
            self.conv_id = data.get("conv_id", "") or ""
            self.task = data.get("task", "") or ""
            _ts_state = data.get("task_state", {})
            self.task_state = _ts_state if isinstance(_ts_state, dict) else {}
            status = data.get("status", "idle")
            if status == "running":
                self.status = "idle"
                self.events.append(
                    {
                        "ts": time.time(),
                        "kind": "system",
                        "message": "上次运行被中断",
                    }
                )
            else:
                self.status = status if status in ("idle", "running") else "idle"


# ---------------------------------------------------------------------------
# 模块级单例 + 兼容包装
# ---------------------------------------------------------------------------
_default = Session()


def init() -> None:
    os.makedirs(STATE_DIR, exist_ok=True)
    os.makedirs(os.path.join(config.AGENT_ROOT, "backups"), exist_ok=True)
    os.makedirs(ARCHIVE_DIR, exist_ok=True)
    _default.load()
    _default.persist()


def load() -> None:
    _default.load()


def persist() -> None:
    _default.persist()


def add_event(kind: str, message: str, **extra: Any) -> None:
    _default.add_event(kind, message, **extra)


def append_history(role: str, content: str) -> None:
    _default.add_message(role, content)


def get_history() -> List[Dict[str, str]]:
    with _default.lock:
        return list(_default.history)


def clear_events() -> None:
    with _default.lock:
        _default.events = []


def get_events(since: int = 0) -> List[Dict[str, Any]]:
    with _default.lock:
        return list(_default.events[since:]) if since >= 0 else list(_default.events)


def event_count() -> int:
    with _default.lock:
        return len(_default.events)


def get_status() -> str:
    with _default.lock:
        return _default.status


def set_status(status: str) -> None:
    _default.set_status(status)


def is_cancelled() -> bool:
    return _default.is_cancelled()


def request_cancel() -> None:
    _default.request_cancel()


def clear_cancel() -> None:
    _default.reset_cancel()


def get_conv_id() -> str:
    with _default.lock:
        return _default.conv_id


def new_conversation(task: str) -> str:
    return _default.new_conversation(task)


def build_messages(system_prompt: str) -> List[Dict[str, str]]:
    return _default.build_messages(system_prompt)


def get_task() -> str:
    with _default.lock:
        return _default.task


def get_task_state() -> Dict[str, Any]:
    with _default.lock:
        return dict(_default.task_state)


def set_task_state(state: Dict[str, Any]) -> None:
    with _default.lock:
        _default.task_state = dict(state or {})


def build_context_preview(system_prompt: str) -> Dict[str, Any]:
    """返回上下文拼接的明细，供前端展示（不调用模型）。"""
    messages = build_messages(system_prompt)
    parts: List[Dict[str, Any]] = []
    for i, m in enumerate(messages):
        role = str(m.get("role", ""))
        content = m.get("content", "")
        if not isinstance(content, str):
            content = str(content)
        if i == 0:
            kind = "system_prompt"
            label = "系统提示词 prompts/system.md"
        elif role == "system" and content.startswith("## 长期记忆"):
            kind = "memory"
            label = "长期记忆（提炼知识）"
        elif role == "user" and content.startswith("当前任务："):
            kind = "task"
            label = "当前任务"
        else:
            kind = "history"
            label = "对话历史窗口"
        parts.append({
            "index": i,
            "kind": kind,
            "label": label,
            "role": role,
            "chars": len(content),
            "content": content,
        })
    total = sum(p["chars"] for p in parts)
    return {
        "conv_id": get_conv_id(),
        "message_count": len(messages),
        "total_chars": total,
        "parts": parts,
    }


def record_step(action: Dict[str, Any], result: Dict[str, Any]) -> None:
    cid = get_conv_id()
    if cid:
        memory.record_step(cid, action, result)


def record_prompt(prompt: str) -> None:
    """从用户 prompt 提炼「任务目标」「用户偏好」写入记忆。"""
    cid = get_conv_id()
    if not cid or not prompt:
        return
    try:
        for section, text in memory.distill_prompt(prompt):
            memory.add_item(cid, section, text)
    except Exception:
        pass


def set_current_process(proc) -> None:
    _default.current_proc = proc


def clear_current_process(proc=None) -> None:
    if proc is None or _default.current_proc is proc:
        _default.current_proc = None


def save() -> None:
    _default.persist()


def set_task(task: str) -> None:
    _default.set_task(task)
