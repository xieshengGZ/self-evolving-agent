"""Core tools migrated from app.actions (Phase 1)."""
from __future__ import annotations

from typing import Any, Dict

from app import actions, config
from app.kernel.api import PluginContext


def _shell(payload: Dict[str, Any]) -> Dict[str, Any]:
    timeout = payload.get("timeout", config.AGENT_CMD_TIMEOUT)
    return actions.do_shell(str(payload.get("command", "")), timeout)


def _write_file(payload: Dict[str, Any]) -> Dict[str, Any]:
    return actions.do_write_file(
        str(payload.get("path", "")), str(payload.get("content", ""))
    )


def _read_file(payload: Dict[str, Any]) -> Dict[str, Any]:
    return actions.do_read_file(str(payload.get("path", "")), payload.get("max_bytes", 20000))


def _self_restart(payload: Dict[str, Any]) -> Dict[str, Any]:
    return actions.do_self_restart(str(payload.get("reason", "")))


def _read_symbol(payload: Dict[str, Any]) -> Dict[str, Any]:
    return actions.do_read_symbol(str(payload.get("symbol", "")))


def _read_file_summary(payload: Dict[str, Any]) -> Dict[str, Any]:
    return actions.do_read_file_summary(str(payload.get("path", "")))


def _rebuild_index(payload: Dict[str, Any]) -> Dict[str, Any]:
    return actions.do_rebuild_index()


def setup(ctx: PluginContext) -> None:
    ctx.tools.register("shell", _shell, description="Run a shell command")
    ctx.tools.register("write_file", _write_file, description="Write a whole file")
    ctx.tools.register("read_file", _read_file, description="Read a file")
    ctx.tools.register("self_restart", _self_restart, description="Restart the agent process")
    ctx.tools.register("read_symbol", _read_symbol, description="Read a symbol's source")
    ctx.tools.register("read_file_summary", _read_file_summary, description="Summarize file symbols")
    ctx.tools.register("rebuild_index", _rebuild_index, description="Rebuild the symbol index")


def teardown(ctx: PluginContext) -> None:
    pass
