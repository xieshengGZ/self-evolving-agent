import asyncio
import json
import os
import threading
import time
from typing import Any, Dict, List, Optional

from app import config, memory, storage as storage_mod

STATE_DIR = os.path.join(config.AGENT_ROOT, "state")
SESSION_FILE = os.path.join(STATE_DIR, "session.json")
ARCHIVE_DIR = os.path.join(STATE_DIR, "archive")

# 工作窗口：只保留最近 N 条动作+结果，不再全量持久化
WINDOW_SIZE = int(os.environ.get("AGENT_WINDOW_SIZE", "8"))
# 单条结果进窗口前的截断长度
RESULT_MAX_CHARS = int(os.environ.get("AGENT_RESULT_MAX_CHARS", "1500"))


class Session:
    def __init__(
        self,
        storage_backend: Optional[storage_mod.Storage] = None,
        auto_migrate: Optional[bool] = None,
    ) -> None:
        self.history: List[Dict[str, str]] = []  # 工作窗口
        self.events: List[Dict[str, Any]] = []
        self.status: str = "idle"
        self.cancel_flag: bool = False
        self.current_proc = None
        self.conv_id: str = ""
        self.task: str = ""
        self.task_state: Dict[str, Any] = {}
        self.lock = threading.RLock()
        self.event_cond = threading.Condition(self.lock)
        self.event_seq: int = 0
        self._event_subscribers: List[Any] = []
        self.storage = storage_backend or storage_mod.Storage()
        self._auto_migrate = (storage_backend is None) if auto_migrate is None else auto_migrate
        try:
            self.storage.init()
        except Exception:  # noqa: BLE001
            pass

    def _ensure_session(self) -> None:
        if self.conv_id:
            return
        with self.lock:
            if self.conv_id:
                return
            self.conv_id = memory.new_conv_id()
            conv_id = self.conv_id
        try:
            self.storage.create_session(conv_id, title=self.task[:120])
        except Exception:  # noqa: BLE001
            pass
        try:
            memory.ensure(conv_id, title=self.task[:120])
        except Exception:  # noqa: BLE001
            pass

    def _save_session(self) -> None:
        if not self.conv_id:
            return
        with self.lock:
            conv_id = self.conv_id
            status = self.status
            task = self.task
            task_state = dict(self.task_state)
        try:
            self.storage.save_session(
                conv_id,
                status=status,
                task=task,
                task_state=task_state,
                title=task[:120],
            )
        except Exception:  # noqa: BLE001
            pass

    # ---------------- events ----------------
    def subscribe_events(self, loop: Any) -> "asyncio.Queue[Dict[str, Any]]":
        queue: "asyncio.Queue[Dict[str, Any]]" = asyncio.Queue(maxsize=500)
        with self.lock:
            self._event_subscribers.append((loop, queue))
        return queue

    def unsubscribe_events(self, queue: "asyncio.Queue[Dict[str, Any]]") -> None:
        with self.lock:
            self._event_subscribers = [
                (loop, item) for loop, item in self._event_subscribers if item is not queue
            ]

    def _publish_event(self, event: Dict[str, Any]) -> None:
        with self.lock:
            subscribers = list(self._event_subscribers)
        for loop, queue in subscribers:
            try:
                loop.call_soon_threadsafe(self._queue_event, queue, event)
            except Exception:  # noqa: BLE001
                continue

    @staticmethod
    def _queue_event(queue: "asyncio.Queue[Dict[str, Any]]", event: Dict[str, Any]) -> None:
        try:
            queue.put_nowait(event)
        except asyncio.QueueFull:
            try:
                queue.get_nowait()
            except asyncio.QueueEmpty:
                pass
            try:
                queue.put_nowait(event)
            except asyncio.QueueFull:
                pass

    def add_event(self, kind: str, message: str, **extra: Any) -> None:
        if not isinstance(message, str):
            message = json.dumps(message, ensure_ascii=False)
        self._ensure_session()
        ts = time.time()
        event_id = 0
        try:
            event_id = self.storage.append_event(
                self.conv_id, kind, message, extra=extra, ts=ts
            )
        except Exception:  # noqa: BLE001
            pass
        with self.event_cond:
            if event_id:
                self.event_seq = max(self.event_seq, event_id)
            else:
                self.event_seq += 1
                event_id = self.event_seq
            event: Dict[str, Any] = {
                "id": event_id,
                "ts": ts,
                "kind": kind,
                "message": message,
            }
            if extra:
                event.update(extra)
            self.events.append(event)
            # events 只保留最近 500 条，避免无限增长
            if len(self.events) > 500:
                self.events = self.events[-500:]
            self.event_cond.notify_all()
        self._publish_event(event)

    # ---------------- window ----------------
    def append_history(self, role: str, content: Any) -> None:
        self.add_message(role, content)

    def add_message(self, role: str, content: Any) -> None:
        if not isinstance(content, str):
            content = json.dumps(content, ensure_ascii=False)
        self._ensure_session()
        try:
            self.storage.append_message(self.conv_id, role, content)
        except Exception:  # noqa: BLE001
            pass
        if len(content) > RESULT_MAX_CHARS:
            content = content[:RESULT_MAX_CHARS] + "…（已截断）"
        with self.lock:
            self.history.append({"role": role, "content": content})
            if len(self.history) > WINDOW_SIZE:
                self.history = self.history[-WINDOW_SIZE:]

    def clear_window(self) -> None:
        with self.lock:
            self.history = []

    def clear_events(self) -> None:
        with self.event_cond:
            self.events = []
            self.event_cond.notify_all()
        try:
            self.storage.clear_events(self.conv_id)
        except Exception:  # noqa: BLE001
            pass

    def wait_events(self, last_id: int = 0, timeout: float = 15.0) -> List[Dict[str, Any]]:
        deadline = time.time() + max(0.1, float(timeout))
        with self.event_cond:
            while True:
                fresh = [
                    event
                    for event in self.events
                    if int(event.get("id") or 0) > int(last_id)
                ]
                if fresh:
                    return fresh
                remaining = deadline - time.time()
                if remaining <= 0:
                    return []
                self.event_cond.wait(remaining)

    def last_event_id(self) -> int:
        with self.lock:
            return int(self.events[-1].get("id") or 0) if self.events else 0

    def set_task(self, task: str) -> None:
        with self.lock:
            self.task = task or ""
        self._save_session()

    def set_task_state(self, state: Dict[str, Any]) -> None:
        with self.lock:
            self.task_state = dict(state or {})
        self._save_session()

    def get_conv_id(self) -> str:
        with self.lock:
            return self.conv_id

    def get_task(self) -> str:
        with self.lock:
            return self.task

    def get_task_state(self) -> Dict[str, Any]:
        with self.lock:
            return dict(self.task_state)

    def get_status(self) -> str:
        with self.lock:
            return self.status

    def new_conversation(self, task: str) -> str:
        """开新对话；旧消息已落在 SQLite，这里只切换当前会话。"""
        with self.lock:
            self.history = []
            self.task = task or ""
            self.task_state = {}
            self.conv_id = memory.new_conv_id()
            conv_id = self.conv_id
        try:
            self.storage.create_session(conv_id, title=(task or "")[:120])
        except Exception:  # noqa: BLE001
            pass
        try:
            memory.ensure(conv_id, title=(task or "")[:120])
        except Exception:  # noqa: BLE001
            pass
        return conv_id

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
        self._save_session()

    def set_last_stop(self, reason: str, detail: str = "") -> None:
        """记录本次运行结束原因，并随 task_state 持久化。"""
        with self.lock:
            state = dict(self.task_state or {})
            if reason:
                state["last_stop"] = {
                    "reason": reason,
                    "detail": detail or "",
                    "at": time.time(),
                }
            else:
                state.pop("last_stop", None)
            self.task_state = state
        self._save_session()

    def get_last_stop(self) -> Dict[str, Any]:
        with self.lock:
            value = (self.task_state or {}).get("last_stop")
            return dict(value) if isinstance(value, dict) else {}

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
            last_stop = (self.task_state or {}).get("last_stop")
            return {
                "status": self.status,
                "history": list(self.history),
                "events": list(events),
                "event_count": len(self.events),
                "last_event_id": self.events[-1].get("id", 0) if self.events else 0,
                "conv_id": self.conv_id,
                "task": self.task,
                "last_stop": dict(last_stop) if isinstance(last_stop, dict) else {},
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
        self._save_session()

    def _load_legacy_json(self) -> bool:
        if not os.path.exists(SESSION_FILE):
            return False
        try:
            with open(SESSION_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
        except Exception:  # noqa: BLE001
            return False
        if not isinstance(data, dict):
            return False
        with self.lock:
            hist = data.get("history", []) or []
            if len(hist) > WINDOW_SIZE:
                try:
                    os.makedirs(ARCHIVE_DIR, exist_ok=True)
                    ts = str(int(time.time()))
                    path = os.path.join(ARCHIVE_DIR, "legacy-" + ts + ".json")
                    with open(path, "w", encoding="utf-8") as f:
                        json.dump({"history": hist}, f, ensure_ascii=False, indent=2)
                except Exception:  # noqa: BLE001
                    pass
                hist = hist[-WINDOW_SIZE:]
            self.history = hist
            self.events = (data.get("events", []) or [])[-500:]
            self.conv_id = data.get("conv_id", "") or ""
            self.task = data.get("task", "") or ""
            state = data.get("task_state", {})
            self.task_state = state if isinstance(state, dict) else {}
            self.event_seq = max(
                [int(ev.get("id") or 0) for ev in self.events if isinstance(ev, dict)] + [0]
            )
            self.status = str(data.get("status") or "idle")
        return True

    def load(self) -> None:
        if self._auto_migrate:
            try:
                self.storage.migrate_json()
            except Exception:  # noqa: BLE001
                pass

        data = None
        try:
            data = self.storage.load_current(window=WINDOW_SIZE, event_limit=500)
        except Exception:  # noqa: BLE001
            data = None

        interrupted = False
        if data is not None:
            with self.lock:
                self.history = data["history"]
                self.events = data["events"]
                self.conv_id = data["conv_id"]
                self.task = data["task"]
                self.task_state = data["task_state"]
                self.event_seq = max(
                    [int(ev.get("id") or 0) for ev in self.events if isinstance(ev, dict)] + [0]
                )
                status = str(data.get("status") or "idle")
                interrupted = status == "running"
                self.status = "idle" if interrupted else (
                    status if status in ("idle", "running") else "idle"
                )
        else:
            if not self._load_legacy_json():
                self._ensure_session()
            with self.lock:
                interrupted = self.status == "running"
                if interrupted:
                    self.status = "idle"
            self.persist()

        if interrupted:
            self.add_event("system", "上次运行被中断")
            self.set_status("idle")
            self.persist()


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
    _default.clear_events()


def get_events(since: int = 0) -> List[Dict[str, Any]]:
    with _default.lock:
        return list(_default.events[since:]) if since >= 0 else list(_default.events)


def snapshot(since: int = 0) -> Dict[str, Any]:
    return _default.snapshot(since)


def event_count() -> int:
    with _default.lock:
        return len(_default.events)


def get_status() -> str:
    with _default.lock:
        return _default.status


def set_status(status: str) -> None:
    _default.set_status(status)


def set_last_stop(reason: str, detail: str = "") -> None:
    _default.set_last_stop(reason, detail)


def get_last_stop() -> Dict[str, Any]:
    return _default.get_last_stop()


def is_cancelled() -> bool:
    return _default.is_cancelled()


def request_cancel() -> None:
    _default.request_cancel()


def clear_cancel() -> None:
    _default.reset_cancel()


def reset_cancel() -> None:
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
    _default.set_task_state(state)


def subscribe_events(loop: Any) -> "asyncio.Queue[Dict[str, Any]]":
    return _default.subscribe_events(loop)


def unsubscribe_events(queue: "asyncio.Queue[Dict[str, Any]]") -> None:
    _default.unsubscribe_events(queue)


def wait_events(last_id: int = 0, timeout: float = 15.0) -> List[Dict[str, Any]]:
    """阻塞等待 id > last_id 的事件；超时返回空列表。"""
    return _default.wait_events(last_id, timeout)


def last_event_id() -> int:
    return _default.last_event_id()


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
