"""记忆查看 API：把 state/memory/*.md 暴露给前端。"""

import os
from typing import Any, Dict, List

from fastapi import Depends, FastAPI, HTTPException
from fastapi.responses import JSONResponse

from app import memory


def _summary(conv_id: str) -> Dict[str, Any]:
    data = memory.load(conv_id)
    meta = dict(data.get("meta") or {})
    sections = dict(data.get("sections") or {})
    counts = {}
    total = 0
    for name, items in sections.items():
        n = len([i for i in (items or []) if i and i != "（暂无）"])
        counts[name] = n
        total += n
    p = memory.path_for(conv_id)
    try:
        size = os.path.getsize(p)
    except Exception:
        size = 0
    return {
        "conv_id": conv_id,
        "title": meta.get("title", ""),
        "created": meta.get("created", ""),
        "updated": meta.get("updated", ""),
        "item_count": total,
        "section_counts": counts,
        "bytes": size,
    }


def register(app: FastAPI, require_token) -> None:
    @app.get("/api/memory", dependencies=[Depends(require_token)])
    def api_memory_list() -> JSONResponse:
        convs: List[Dict[str, Any]] = []
        for cid in memory.list_convs():
            try:
                convs.append(_summary(cid))
            except Exception:
                continue
        convs.sort(key=lambda c: c.get("updated") or "", reverse=True)
        return JSONResponse({"ok": True, "conversations": convs})

    @app.get("/api/memory/{conv_id}", dependencies=[Depends(require_token)])
    def api_memory_detail(conv_id: str) -> JSONResponse:
        safe = memory._safe_conv_id(conv_id)
        if safe != conv_id:
            raise HTTPException(status_code=400, detail="invalid conv_id")
        if not os.path.isfile(memory.path_for(conv_id)):
            raise HTTPException(status_code=404, detail="memory not found")
        data = memory.load(conv_id)
        return JSONResponse({
            "ok": True,
            "conv_id": conv_id,
            "meta": data.get("meta") or {},
            "sections": data.get("sections") or {},
            "markdown": memory.read_text(conv_id, max_bytes=200000),
        })
