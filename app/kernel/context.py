"""The only API plugins should depend on."""
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, Dict

from app.kernel.bus import EventBus
from app.kernel.registry import (
    ContextRegistry,
    HookBus,
    HttpRegistry,
    PlannerRegistry,
    RegistrySet,
    ToolRegistry,
)
from app.kernel.store import Store


@dataclass
class PluginContext:
    name: str
    version: str
    config: Dict[str, Any]
    layer: RegistrySet
    store: Store
    bus: EventBus
    log: logging.Logger

    @property
    def tools(self) -> ToolRegistry:
        return self.layer.tools

    @property
    def hooks(self) -> HookBus:
        return self.layer.hooks

    @property
    def http(self) -> HttpRegistry:
        return self.layer.http

    @property
    def planners(self) -> PlannerRegistry:
        return self.layer.planners

    @property
    def context(self) -> ContextRegistry:
        return self.layer.context

    @property
    def llm(self) -> Any:
        from app import deepseek

        return deepseek
