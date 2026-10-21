"""Minimal demo plugin used to verify hot reload without a restart."""
from __future__ import annotations

import time
from typing import Any, Dict

from app.kernel.api import PluginContext

VERSION = "0.1.0"


def _ping(ctx: PluginContext, payload: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "ok": True,
        "plugin": ctx.name,
        "version": ctx.version,
        "reply": ctx.config.get("reply", "pong"),
        "echo": payload.get("echo"),
        "ts": time.time(),
    }


def setup(ctx: PluginContext) -> None:
    ctx.tools.register(
        "hello_ping",
        lambda payload: _ping(ctx, payload),
        description="Demo tool proving hot-loaded plugins are callable",
    )
    ctx.http.register("ping", lambda payload: _ping(ctx, payload))
    ctx.hooks.on("kernel.started", lambda **kwargs: {"plugin": ctx.name})


def teardown(ctx: PluginContext) -> None:
    ctx.log.info("hello plugin unloaded")
