"""Plugin-facing registries.

Each plugin gets its own RegistrySet. The kernel keeps an ordered list of
these layers and resolves capabilities at call time, so a plugin can be
replaced atomically without restarting the process.
"""
from __future__ import annotations

import logging
import threading
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional

log = logging.getLogger("kernel.registry")

ToolHandler = Callable[[Dict[str, Any]], Any]
HttpHandler = Callable[[Dict[str, Any]], Any]
HookHandler = Callable[..., Any]


class ToolRegistry:
    def __init__(self) -> None:
        self._tools: Dict[str, Dict[str, Any]] = {}
        self._lock = threading.RLock()

    def register(
        self,
        name: str,
        handler: ToolHandler,
        *,
        description: str = "",
        schema: Optional[Dict[str, Any]] = None,
    ) -> None:
        if not name:
            raise ValueError("tool name is required")
        if not callable(handler):
            raise TypeError("tool handler must be callable")
        with self._lock:
            self._tools[name] = {
                "handler": handler,
                "description": description,
                "schema": schema,
            }

    def unregister(self, name: str) -> None:
        with self._lock:
            self._tools.pop(name, None)

    def has(self, name: str) -> bool:
        with self._lock:
            return name in self._tools

    def names(self) -> List[str]:
        with self._lock:
            return sorted(self._tools)

    def describe(self) -> Dict[str, Any]:
        with self._lock:
            return {
                name: {
                    "description": item.get("description", ""),
                    "schema": item.get("schema"),
                }
                for name, item in self._tools.items()
            }

    def execute(self, name: str, payload: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        with self._lock:
            tool = self._tools.get(name)
        if tool is None:
            return {"error": f"unknown action: {name}"}
        try:
            result = tool["handler"](payload or {})
        except Exception as exc:  # noqa: BLE001
            log.exception("tool %s failed", name)
            return {"error": f"{type(exc).__name__}: {exc}"}
        if not isinstance(result, dict):
            return {"result": result}
        return result


class HookBus:
    def __init__(self) -> None:
        self._hooks: Dict[str, List[HookHandler]] = {}
        self._lock = threading.RLock()

    def on(self, event: str, handler: HookHandler) -> None:
        if not callable(handler):
            raise TypeError("hook handler must be callable")
        with self._lock:
            self._hooks.setdefault(event, []).append(handler)

    def off(self, event: str, handler: HookHandler) -> None:
        with self._lock:
            handlers = self._hooks.get(event)
            if not handlers:
                return
            self._hooks[event] = [h for h in handlers if h is not handler]

    def emit(self, event: str, **kwargs: Any) -> List[Any]:
        with self._lock:
            handlers = list(self._hooks.get(event, []))
        results: List[Any] = []
        for handler in handlers:
            try:
                results.append(handler(**kwargs))
            except Exception:  # noqa: BLE001
                log.exception("hook %s failed", event)
        return results

    def describe(self) -> Dict[str, int]:
        with self._lock:
            return {event: len(handlers) for event, handlers in self._hooks.items()}


class HttpRegistry:
    def __init__(self) -> None:
        self._ops: Dict[str, Dict[str, Any]] = {}
        self._lock = threading.RLock()

    def register(self, op: str, handler: HttpHandler, *, description: str = "") -> None:
        if not op:
            raise ValueError("http op name is required")
        if not callable(handler):
            raise TypeError("http handler must be callable")
        with self._lock:
            self._ops[op] = {"handler": handler, "description": description}

    def unregister(self, op: str) -> None:
        with self._lock:
            self._ops.pop(op, None)

    def get(self, op: str) -> Optional[HttpHandler]:
        with self._lock:
            item = self._ops.get(op)
        return item["handler"] if item else None

    def ops(self) -> List[str]:
        with self._lock:
            return sorted(self._ops)

    def describe(self) -> Dict[str, str]:
        with self._lock:
            return {op: item.get("description", "") for op, item in self._ops.items()}


class PlannerRegistry:
    def __init__(self) -> None:
        self._planners: Dict[str, Any] = {}
        self._lock = threading.RLock()

    def register(self, name: str, planner: Any) -> None:
        if not name:
            raise ValueError("planner name is required")
        with self._lock:
            self._planners[name] = planner

    def unregister(self, name: str) -> None:
        with self._lock:
            self._planners.pop(name, None)

    def get(self, name: str) -> Any:
        with self._lock:
            return self._planners.get(name)

    def names(self) -> List[str]:
        with self._lock:
            return sorted(self._planners)


class ContextRegistry:
    def __init__(self) -> None:
        self._builders: List[tuple[int, Any]] = []
        self._lock = threading.RLock()

    def register(self, builder: Any, order: int = 100) -> None:
        with self._lock:
            self._builders.append((int(order), builder))

    def unregister(self, builder: Any) -> None:
        with self._lock:
            self._builders = [(o, b) for o, b in self._builders if b is not builder]

    def ordered(self) -> List[Any]:
        with self._lock:
            return [builder for _, builder in sorted(self._builders, key=lambda item: item[0])]

    def describe(self) -> List[Any]:
        with self._lock:
            return [
                {"order": order, "name": getattr(builder, "__name__", type(builder).__name__)}
                for order, builder in sorted(self._builders, key=lambda item: item[0])
            ]


@dataclass
class RegistrySet:
    tools: ToolRegistry = field(default_factory=ToolRegistry)
    hooks: HookBus = field(default_factory=HookBus)
    http: HttpRegistry = field(default_factory=HttpRegistry)
    planners: PlannerRegistry = field(default_factory=PlannerRegistry)
    context: ContextRegistry = field(default_factory=ContextRegistry)
