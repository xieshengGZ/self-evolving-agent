"""Kernel runtime singleton."""
from __future__ import annotations

import os
import threading
from typing import Any, Dict, Optional

from app import config
from app.kernel.bus import EventBus
from app.kernel.plugins import PluginManager
from app.kernel.store import Store


class KernelRuntime:
    def __init__(self, plugins_dir: Optional[str | os.PathLike[str]] = None) -> None:
        self.store = Store()
        self.bus = EventBus(self.store)
        self.plugins = PluginManager(plugins_dir or config.PLUGINS_DIR, self.store, self.bus)
        self._started = False
        self._lock = threading.Lock()

    def start(self, *, announce: bool = True) -> None:
        with self._lock:
            if self._started:
                return
            self.plugins.load_all()
            self._started = True

        if announce:
            self.bus.publish(
                "kernel.started",
                {"plugins": [p["name"] for p in self.plugins.list_plugins()]},
                persist=True,
            )
        try:
            if int(config.AGENT_PLUGIN_AUTORELOAD):
                self.plugins.start_watcher()
        except Exception:  # noqa: BLE001
            pass

    def stop(self) -> None:
        self.plugins.stop_watcher()

    def get_planner(self, name: str = "default") -> Any:
        return self.plugins.get_planner(name)

    def emit_hook(self, event: str, **kwargs: Any) -> list[Any]:
        return self.plugins.emit_hook(event, **kwargs)

    def execute_tool(self, action: Dict[str, Any]) -> Dict[str, Any]:
        if not isinstance(action, dict):
            return {"error": "动作必须是 JSON 对象"}
        name = action.get("action")
        if not name:
            return {"error": "动作缺少 action 字段"}
        if name == "done":
            return {"result_of": "done", "message": str(action.get("message", ""))}
        return self.plugins.execute_tool(str(name), action)

    def snapshot(self) -> Dict[str, Any]:
        return self.plugins.snapshot()


_runtime: Optional[KernelRuntime] = None
_runtime_lock = threading.Lock()


def get_runtime() -> KernelRuntime:
    global _runtime
    with _runtime_lock:
        if _runtime is None:
            _runtime = KernelRuntime()
        return _runtime


def reset_runtime() -> None:
    """Test helper: drop the singleton so a new plugins dir can be used."""
    global _runtime
    with _runtime_lock:
        if _runtime is not None:
            _runtime.stop()
        _runtime = None
