"""Token usage stats for DeepSeek calls."""
import json
import os
import threading
import time
from typing import Any, Dict, List, Optional

from app import config

USAGE_FILE = os.path.join(config.STATE_DIR, "usage.json")
MAX_RECORDS = 2000

_lock = threading.RLock()

# 价格（元 / 百万 token），可用环境变量覆盖
PRICE_INPUT = float(os.environ.get("AGENT_PRICE_INPUT", "2.0"))
PRICE_OUTPUT = float(os.environ.get("AGENT_PRICE_OUTPUT", "8.0"))


def _load() -> Dict[str, Any]:
    try:
        with open(USAGE_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
        if isinstance(data, dict) and isinstance(data.get("records"), list):
            return data
    except Exception:
        pass
    return {"records": []}


def _save(data: Dict[str, Any]) -> None:
    try:
        os.makedirs(config.STATE_DIR, exist_ok=True)
        tmp = USAGE_FILE + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        os.replace(tmp, USAGE_FILE)
    except Exception:
        pass


def record(usage: Optional[Dict[str, Any]], conv_id: str = "", source: str = "") -> None:
    """记录一次调用的 usage。usage 为 DeepSeek 响应中的 usage 字段。"""
    if not isinstance(usage, dict):
        return
    pt = int(usage.get("prompt_tokens") or 0)
    ct = int(usage.get("completion_tokens") or 0)
    tt = int(usage.get("total_tokens") or (pt + ct))
    if pt == 0 and ct == 0 and tt == 0:
        return
    rec = {
        "ts": time.time(),
        "conv_id": conv_id or "",
        "source": source or "",
        "prompt_tokens": pt,
        "completion_tokens": ct,
        "total_tokens": tt,
        "prompt_cache_hit_tokens": int(usage.get("prompt_cache_hit_tokens") or 0),
        "prompt_cache_miss_tokens": int(usage.get("prompt_cache_miss_tokens") or 0),
    }
    with _lock:
        data = _load()
        data["records"].append(rec)
        if len(data["records"]) > MAX_RECORDS:
            data["records"] = data["records"][-MAX_RECORDS:]
        _save(data)


def _cost(pt: int, ct: int) -> float:
    return pt / 1_000_000 * PRICE_INPUT + ct / 1_000_000 * PRICE_OUTPUT


def _agg(records: List[Dict[str, Any]]) -> Dict[str, Any]:
    pt = sum(int(r.get("prompt_tokens") or 0) for r in records)
    ct = sum(int(r.get("completion_tokens") or 0) for r in records)
    tt = sum(int(r.get("total_tokens") or 0) for r in records)
    hit = sum(int(r.get("prompt_cache_hit_tokens") or 0) for r in records)
    return {
        "calls": len(records),
        "prompt_tokens": pt,
        "completion_tokens": ct,
        "total_tokens": tt,
        "cache_hit_tokens": hit,
        "cost_cny": round(_cost(pt, ct), 6),
    }


def summary(conv_id: str = "", limit: int = 50) -> Dict[str, Any]:
    """返回全局统计 + 指定会话统计 + 最近记录。"""
    with _lock:
        data = _load()
    records = data.get("records", [])
    conv_records = [r for r in records if conv_id and r.get("conv_id") == conv_id]
    return {
        "ok": True,
        "price": {"input_per_million": PRICE_INPUT, "output_per_million": PRICE_OUTPUT},
        "global": _agg(records),
        "conversation": _agg(conv_records),
        "conv_id": conv_id,
        "recent": records[-limit:][::-1],
    }


def reset() -> Dict[str, Any]:
    with _lock:
        _save({"records": []})
    return {"ok": True}
