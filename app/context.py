"""Context assembly with a section model, token budget, and artifact offload.

The kernel owns ordering, budgeting, and rendering. Context plugins only
produce sections, so they can be hot-replaced without touching the loop.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from app import config, session


PRIORITY = {
    "system": 1000,
    "task": 900,
    "plan": 700,
    "memory": 600,
    "history": 500,
}

ROLE_PRIORITY = {
    "system": 1000,
    "user": 900,
    "assistant": 500,
}


@dataclass
class Section:
    kind: str
    content: str
    role: str = "system"
    label: str = ""
    priority: int = 100
    order: int = 100
    required: bool = False
    compressible: bool = True
    source: str = ""
    metadata: Dict[str, Any] = field(default_factory=dict)
    included: bool = True
    truncated: bool = False

    def __post_init__(self) -> None:
        self.kind = str(self.kind or "section")
        self.content = str(self.content or "")
        self.role = str(self.role or "system")
        if self.role not in ("system", "user", "assistant"):
            self.role = "system"
        self.label = str(self.label or self.kind)
        self.priority = int(self.priority)
        self.order = int(self.order)
        self.required = bool(self.required)
        self.compressible = bool(self.compressible)
        self.source = str(self.source or "")
        if not isinstance(self.metadata, dict):
            self.metadata = {}

    @classmethod
    def from_any(cls, value: Any, *, order: int = 100, source: str = "") -> "Section":
        if isinstance(value, Section):
            value.source = value.source or source
            return value
        if not isinstance(value, dict):
            raise ValueError("context section must be a dict or Section")
        kind = str(value.get("kind") or "section")
        role = str(value.get("role") or ("user" if kind == "task" else "system"))
        priority = value.get("priority", PRIORITY.get(kind, ROLE_PRIORITY.get(role, 100)))
        return cls(
            kind=kind,
            content=value.get("content", ""),
            role=role,
            label=str(value.get("label") or kind),
            priority=int(priority),
            order=int(value.get("order", order)),
            required=bool(value.get("required", False)),
            compressible=bool(value.get("compressible", True)),
            source=str(value.get("source") or source),
            metadata=value.get("metadata") if isinstance(value.get("metadata"), dict) else {},
        )

    def to_message(self) -> Dict[str, str]:
        return {"role": self.role, "content": self.content}

    def to_dict(self, *, with_content: bool = False) -> Dict[str, Any]:
        item: Dict[str, Any] = {
            "kind": self.kind,
            "label": self.label,
            "role": self.role,
            "priority": self.priority,
            "order": self.order,
            "required": self.required,
            "compressible": self.compressible,
            "source": self.source,
            "included": self.included,
            "truncated": self.truncated,
            "chars": len(self.content),
            "tokens": estimate_tokens(self.content),
        }
        if self.metadata:
            item["metadata"] = dict(self.metadata)
        if with_content:
            item["content"] = self.content
        return item


def estimate_tokens(text: Any) -> int:
    """Conservative estimate: ASCII ~4 chars/token, non-ASCII ~1 char/token."""
    if not isinstance(text, str):
        text = str(text or "")
    if not text:
        return 0
    ascii_count = sum(1 for char in text if ord(char) < 128)
    other = len(text) - ascii_count
    return max(1, (ascii_count + 3) // 4 + other)


def _truncate_to_tokens(text: str, limit: int, marker: str = "\n…（已按上下文预算截断）") -> str:
    if estimate_tokens(text) <= limit:
        return text
    if limit <= 8:
        return ""
    marker = marker if estimate_tokens(marker) < limit else "\n…"
    budget = max(0, limit - estimate_tokens(marker))
    low, high = 0, len(text)
    while low < high:
        middle = (low + high + 1) // 2
        if estimate_tokens(text[:middle]) <= budget:
            low = middle
        else:
            high = middle - 1
    return text[:low] + marker


def _safe_conv_id(conv_id: str) -> str:
    value = re.sub(r"[^A-Za-z0-9_.-]", "_", (conv_id or "").strip())
    value = value.replace("..", "__")
    return (value or "default")[:80]


def offload_result(
    action: Dict[str, Any],
    result: Any,
    *,
    conv_id: str = "",
) -> tuple[Any, Optional[Dict[str, Any]]]:
    """Store a large tool result on disk and return a compact context summary."""
    if not int(getattr(config, "AGENT_ARTIFACT_OFFLOAD", 1)):
        return result, None
    try:
        full = json.dumps(result, ensure_ascii=False)
    except Exception:  # noqa: BLE001
        full = str(result)
    threshold = max(256, int(getattr(config, "AGENT_ARTIFACT_THRESHOLD", 2000)))
    if len(full) < threshold:
        return result, None

    cid = conv_id or session.get_conv_id() or "default"
    root = os.path.join(config.STATE_DIR, "artifacts", _safe_conv_id(cid))
    try:
        os.makedirs(root, exist_ok=True)
    except Exception:  # noqa: BLE001
        return result, None
    digest = hashlib.sha1(full.encode("utf-8", errors="replace")).hexdigest()[:10]
    name = f"{int(time.time() * 1000)}-{digest}.json"
    path = os.path.join(root, name)
    try:
        with open(path, "w", encoding="utf-8") as handle:
            handle.write(full)
    except Exception:  # noqa: BLE001
        return result, None

    summary: Dict[str, Any] = {
        "result_of": (result.get("result_of") if isinstance(result, dict) else "") or action.get("action", ""),
        "artifact": os.path.abspath(path),
        "artifact_rel": os.path.relpath(path, config.AGENT_ROOT),
        "artifact_bytes": len(full.encode("utf-8", errors="replace")),
        "context_truncated": True,
    }
    if isinstance(result, dict):
        for key in ("exit_code", "timed_out", "path", "bytes_read", "bytes_written", "truncated"):
            if key in result:
                summary[key] = result[key]
        error = result.get("error")
        if isinstance(error, str) and error:
            summary["error"] = error[:1200]
        for key in ("stdout", "stderr", "content"):
            value = result.get(key)
            if isinstance(value, str) and value:
                tail = value[-1200:] if key != "content" else value[:1200]
                summary[key + "_preview"] = tail
    return summary, {
        "path": os.path.abspath(path),
        "rel": os.path.relpath(path, config.AGENT_ROOT),
        "bytes": summary["artifact_bytes"],
    }


def _fallback_sections(system_prompt: str) -> List[Section]:
    try:
        data = session.build_context_preview(system_prompt)
        parts = data.get("parts") or []
    except Exception:  # noqa: BLE001
        parts = []
    sections: List[Section] = []
    for index, part in enumerate(parts):
        if not isinstance(part, dict):
            continue
        kind = str(part.get("kind") or "history")
        role = str(part.get("role") or ("user" if kind == "task" else "system"))
        priority = PRIORITY.get(kind, ROLE_PRIORITY.get(role, 100))
        sections.append(
            Section(
                kind=kind,
                content=part.get("content", ""),
                role=role,
                label=str(part.get("label") or kind),
                priority=priority,
                order=index,
                required=kind in ("system", "system_prompt", "task"),
                compressible=kind not in ("system", "system_prompt"),
                source="session.fallback",
            )
        )
    if not sections:
        sections.append(
            Section(
                kind="system",
                content=system_prompt,
                label="系统提示词",
                priority=PRIORITY["system"],
                order=0,
                required=True,
                compressible=False,
                source="session.fallback",
            )
        )
    return sections


def _call_builder(builder: Any, store: Any, system_prompt: str) -> List[Section]:
    if hasattr(builder, "build") and callable(builder.build):
        raw = builder.build(store, system_prompt)
    else:
        raw = builder(store, system_prompt)
    if raw is None:
        return []
    if isinstance(raw, dict):
        raw = [raw]
    if not isinstance(raw, (list, tuple)):
        raise ValueError("context builder must return a list of sections")
    return [Section.from_any(item) for item in raw]


def _normalize_builders(runtime: Any) -> List[tuple[int, str, Any]]:
    if runtime is None or not hasattr(runtime, "context_builders"):
        return []
    try:
        raw = runtime.context_builders()
    except Exception:  # noqa: BLE001
        return []
    builders: List[tuple[int, str, Any]] = []
    for item in raw or []:
        if not isinstance(item, (list, tuple)):
            continue
        if len(item) >= 3:
            order, name, builder = item[0], item[1], item[2]
        elif len(item) == 2:
            order, builder = item
            name = getattr(builder, "__name__", type(builder).__name__)
        else:
            continue
        try:
            builders.append((int(order), str(name), builder))
        except (TypeError, ValueError):
            continue
    return sorted(builders, key=lambda row: row[0])


def _ensure_core_sections(sections: List[Section], system_prompt: str) -> List[Section]:
    result = list(sections)
    for section in result:
        if section.kind in ("system", "system_prompt"):
            section.required = True
            section.compressible = False
        elif section.kind == "task":
            section.required = True
    if not any(section.kind in ("system", "system_prompt") for section in result):
        result.append(
            Section(
                kind="system",
                content=system_prompt,
                label="系统提示词",
                priority=PRIORITY["system"],
                order=-100,
                required=True,
                compressible=False,
                source="kernel",
            )
        )
    task = (session.get_task() or "").strip()
    if task and not any(section.kind == "task" for section in result):
        result.append(
            Section(
                kind="task",
                content="当前任务：" + task,
                role="user",
                label="当前任务",
                priority=PRIORITY["task"],
                order=-90,
                required=True,
                compressible=True,
                source="kernel",
            )
        )
    return result


def _use_compress_hook(
    runtime: Any,
    sections: List[Section],
    budget: int,
    used: int,
) -> tuple[List[Section], bool]:
    if runtime is None or not hasattr(runtime, "emit_hook"):
        return sections, False
    try:
        results = runtime.emit_hook(
            "context.compress",
            sections=[section.to_dict(with_content=True) for section in sections],
            budget=budget,
            used=used,
        )
    except Exception:  # noqa: BLE001
        return sections, False
    for result in results or []:
        if isinstance(result, (list, tuple)) and result:
            try:
                return [Section.from_any(item) for item in result], True
            except Exception:  # noqa: BLE001
                continue
    return sections, False


def _fit_sections(
    sections: List[Section],
    budget: int,
    runtime: Any = None,
    system_prompt: str = "",
    pressure: float = 0.7,
) -> tuple[List[Section], bool, bool]:
    total = sum(estimate_tokens(section.content) for section in sections)
    compressed = False
    pressure_limit = max(1, int(budget * pressure))
    if total > pressure_limit:
        sections, hook_used = _use_compress_hook(runtime, sections, budget, total)
        compressed = compressed or hook_used
        if system_prompt:
            sections = _ensure_core_sections(sections, system_prompt)
        total = sum(estimate_tokens(section.content) for section in sections)

    required = [section for section in sections if section.required]
    optional = [section for section in sections if not section.required]
    optional.sort(key=lambda section: (-section.priority, section.order))

    selected: List[Section] = []
    used = 0
    for section in required:
        section.included = True
        selected.append(section)
        used += estimate_tokens(section.content)

    remaining = max(0, budget - used)
    for section in optional:
        tokens = estimate_tokens(section.content)
        if tokens <= remaining:
            section.included = True
            section.truncated = False
            selected.append(section)
            remaining -= tokens
            used += tokens
            continue
        if section.compressible and remaining >= 32:
            section.content = _truncate_to_tokens(section.content, remaining)
            section.truncated = True
            section.included = True
            actual = estimate_tokens(section.content)
            selected.append(section)
            used += actual
            remaining = max(0, remaining - actual)
        else:
            section.included = False
            section.truncated = False

    selected.sort(key=lambda section: (section.order, -section.priority))
    return selected, compressed, used > budget


def build(system_prompt: str, *, runtime: Any = None) -> Dict[str, Any]:
    budget = max(256, int(getattr(config, "AGENT_CONTEXT_BUDGET", 12000)))
    builder_errors: List[str] = []
    sections: List[Section] = []
    builders = _normalize_builders(runtime)

    store = getattr(runtime, "store", None) if runtime is not None else None
    for order, name, builder in builders:
        try:
            produced = _call_builder(builder, store, system_prompt)
            for section in produced:
                section.source = section.source or name
            sections.extend(produced)
        except Exception as exc:  # noqa: BLE001
            builder_errors.append(f"{name}: {type(exc).__name__}: {exc}")

    fallback = not sections
    if fallback:
        sections = _fallback_sections(system_prompt)

    sections = _ensure_core_sections(sections, system_prompt)
    pressure = float(getattr(config, "AGENT_CONTEXT_PRESSURE", 0.7))
    selected, compressed, over_budget = _fit_sections(
        sections, budget, runtime, system_prompt, pressure
    )
    compressed = compressed or any(section.truncated for section in selected)
    compressed = compressed or any(not section.included for section in sections)
    messages = [section.to_message() for section in selected]

    used = sum(estimate_tokens(section.content) for section in selected)
    pressure_hit = used >= max(1, int(budget * pressure))
    ledger = {
        "budget_tokens": budget,
        "used_tokens": used,
        "utilization": round(used / budget, 4) if budget else 0.0,
        "pressure": bool(pressure_hit),
        "compressed": bool(compressed),
        "over_budget": bool(over_budget),
        "fallback": bool(fallback),
        "message_count": len(messages),
        "total_chars": sum(len(section.content) for section in selected),
        "included_sections": len(selected),
        "omitted_sections": sum(1 for section in sections if not section.included),
        "builder_errors": builder_errors,
        "sections": [
            section.to_dict()
            for section in sorted(sections, key=lambda item: (item.order, -item.priority))
        ],
    }
    return {
        "messages": messages,
        "sections": [section.to_dict(with_content=True) for section in selected],
        "ledger": ledger,
    }


def preview(system_prompt: str, *, runtime: Any = None) -> Dict[str, Any]:
    built = build(system_prompt, runtime=runtime)
    messages = built["messages"]
    ledger = built["ledger"]
    parts: List[Dict[str, Any]] = []
    for index, section in enumerate(built["sections"]):
        parts.append(
            {
                "index": index,
                "kind": section.get("kind"),
                "label": section.get("label"),
                "role": section.get("role"),
                "priority": section.get("priority"),
                "required": section.get("required"),
                "truncated": section.get("truncated"),
                "source": section.get("source"),
                "chars": section.get("chars"),
                "tokens": section.get("tokens"),
                "content": section.get("content", ""),
            }
        )
    return {
        "messages": messages,
        "message_count": len(messages),
        "total_chars": sum(len(message.get("content", "")) for message in messages),
        "parts": parts,
        "ledger": ledger,
    }
