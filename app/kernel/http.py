"""Plugin HTTP surface.

Routes are intentionally fixed: plugin ops are looked up at request time, so
hot replacement never mutates FastAPI's route table.
"""
from __future__ import annotations

import inspect
import json
from typing import Any, Callable

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse

from app.kernel.runtime import KernelRuntime


def register_plugin_routes(
    app: FastAPI,
    runtime: KernelRuntime,
    require_token: Callable[..., Any],
) -> None:
    @app.get("/api/plugins", dependencies=[Depends(require_token)])
    def api_plugins() -> JSONResponse:
        return JSONResponse(
            {
                "ok": True,
                "plugins": runtime.plugins.list_plugins(),
                "tools": runtime.plugins.tool_names(),
                "errors": runtime.plugins.errors(),
            }
        )

    @app.post("/api/plugins/{name}/reload", dependencies=[Depends(require_token)])
    def api_plugin_reload(name: str) -> JSONResponse:
        try:
            loaded = runtime.plugins.reload(name)
        except Exception as exc:  # noqa: BLE001
            raise HTTPException(status_code=400, detail=f"{type(exc).__name__}: {exc}")
        return JSONResponse(
            {
                "ok": True,
                "plugin": loaded.manifest.name,
                "version": loaded.manifest.version,
                "tools": loaded.layer.tools.names(),
                "http": loaded.layer.http.ops(),
            }
        )

    @app.post("/api/plugin/{plugin}/{op}", dependencies=[Depends(require_token)])
    async def api_plugin_call(plugin: str, op: str, request: Request) -> JSONResponse:
        handler = runtime.plugins.get_http(plugin, op)
        if handler is None:
            raise HTTPException(status_code=404, detail=f"unknown plugin op: {plugin}/{op}")

        raw = await request.body()
        payload: dict[str, Any] = {}
        if raw:
            try:
                payload = json.loads(raw)
            except Exception as exc:  # noqa: BLE001
                raise HTTPException(status_code=400, detail=f"invalid json: {exc}")
            if not isinstance(payload, dict):
                raise HTTPException(status_code=400, detail="payload must be a JSON object")

        try:
            result = handler(payload)
            if inspect.isawaitable(result):
                result = await result
        except Exception as exc:  # noqa: BLE001
            raise HTTPException(status_code=500, detail=f"{type(exc).__name__}: {exc}")

        if isinstance(result, dict):
            return JSONResponse(result)
        return JSONResponse({"ok": True, "result": result})
