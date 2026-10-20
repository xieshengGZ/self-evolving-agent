"""语义/长期记忆：每个对话一个 Markdown 文件，只存提炼后的知识。

目录结构：state/memory/<conv_id>.md
文件内容（人可读 Markdown）：

    # 对话 <conv_id>
    - created: 2025-01-01 12:00:00
    - updated: 2025-01-01 12:05:00
    - title: <任务标题>

    ## 任务目标
    - ...

    ## 已确认事实
    - ...

    ## 项目架构
    - ...

    ## 用户偏好
    - ...

    ## 关键决策
    - ...

    ## 未完成事项
    - ...

设计原则：
- 只存提炼后的知识，不存原始对话/命令输出。
- 写入去重，单文件有上限，超出时保留最新条目。
- 读取时返回完整 Markdown 文本，供拼进 system 上下文。
"""

import os
import re
import time
from typing import Dict, List, Optional

from app import config

MEMORY_DIR = os.path.join(config.STATE_DIR, "memory")

SECTIONS: List[str] = [
    "任务目标",
    "已确认事实",
    "项目架构",
    "用户偏好",
    "关键决策",
    "未完成事项",
]

MAX_FILE_BYTES = 8000
MAX_ITEMS_PER_SECTION = 40
MAX_ITEM_CHARS = 300


def _ensure_dir() -> None:
    os.makedirs(MEMORY_DIR, exist_ok=True)


def _safe_conv_id(conv_id: str) -> str:
    cid = re.sub(r"[^A-Za-z0-9_.-]", "_", (conv_id or "").strip())
    if not cid:
        cid = time.strftime("%Y%m%d-%H%M%S")
    return cid[:80]


def path_for(conv_id: str) -> str:
    return os.path.join(MEMORY_DIR, _safe_conv_id(conv_id) + ".md")


def new_conv_id() -> str:
    return time.strftime("%Y%m%d-%H%M%S")


def _now() -> str:
    return time.strftime("%Y-%m-%d %H:%M:%S")


def _clean_item(text: str) -> str:
    if not text:
        return ""
    t = str(text).replace("\r", " ").replace("\n", " ").strip()
    t = re.sub(r"\s+", " ", t)
    if len(t) > MAX_ITEM_CHARS:
        t = t[:MAX_ITEM_CHARS] + "…"
    return t


def _parse(text: str) -> Dict[str, object]:
    """把 Markdown 解析成 {meta:{}, sections:{name:[items]}}。"""
    meta: Dict[str, str] = {}
    sections: Dict[str, List[str]] = {s: [] for s in SECTIONS}
    current: Optional[str] = None
    for raw in text.splitlines():
        line = raw.rstrip()
        if line.startswith("- ") and current is None and ":" in line:
            k, v = line[2:].split(":", 1)
            meta[k.strip()] = v.strip()
            continue
        if line.startswith("## "):
            name = line[3:].strip()
            current = name if name in sections else None
            if current is not None:
                sections.setdefault(current, [])
            continue
        if current is not None and line.startswith("- "):
            item = line[2:].strip()
            if item:
                sections[current].append(item)
    return {"meta": meta, "sections": sections}


def _render(conv_id: str, meta: Dict[str, str], sections: Dict[str, List[str]]) -> str:
    lines: List[str] = []
    lines.append("# 对话 " + _safe_conv_id(conv_id))
    lines.append("- created: " + meta.get("created", _now()))
    lines.append("- updated: " + meta.get("updated", _now()))
    lines.append("- title: " + (meta.get("title") or ""))
    lines.append("")
    for name in SECTIONS:
        items = sections.get(name, []) or []
        lines.append("## " + name)
        if not items:
            lines.append("- （暂无）")
        else:
            for it in items:
                lines.append("- " + it)
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def load(conv_id: str) -> Dict[str, object]:
    p = path_for(conv_id)
    if not os.path.isfile(p):
        return {
            "meta": {"created": _now(), "updated": _now(), "title": ""},
            "sections": {s: [] for s in SECTIONS},
        }
    try:
        with open(p, "r", encoding="utf-8") as f:
            text = f.read()
    except Exception:
        return {
            "meta": {"created": _now(), "updated": _now(), "title": ""},
            "sections": {s: [] for s in SECTIONS},
        }
    return _parse(text)


def save(conv_id: str, data: Dict[str, object]) -> str:
    _ensure_dir()
    meta = dict(data.get("meta") or {})
    sections = dict(data.get("sections") or {})
    meta["updated"] = _now()
    meta.setdefault("created", _now())
    for name in SECTIONS:
        items = list(sections.get(name) or [])
        if len(items) > MAX_ITEMS_PER_SECTION:
            items = items[-MAX_ITEMS_PER_SECTION:]
        sections[name] = items
    text = _render(conv_id, meta, sections)
    # 超限时从最早的条目开始丢
    while len(text.encode("utf-8")) > MAX_FILE_BYTES:
        dropped = False
        for name in SECTIONS:
            items = sections.get(name) or []
            if len(items) > 1:
                sections[name] = items[1:]
                dropped = True
                break
        if not dropped:
            break
        text = _render(conv_id, meta, sections)
    p = path_for(conv_id)
    tmp = p + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        f.write(text)
    os.replace(tmp, p)
    return p


def ensure(conv_id: str, title: str = "") -> str:
    """确保记忆文件存在；返回路径。"""
    _ensure_dir()
    p = path_for(conv_id)
    if not os.path.isfile(p):
        data = {
            "meta": {"created": _now(), "updated": _now(), "title": title or ""},
            "sections": {s: [] for s in SECTIONS},
        }
        save(conv_id, data)
    elif title:
        data = load(conv_id)
        meta = dict(data.get("meta") or {})
        if not meta.get("title"):
            meta["title"] = title
            data["meta"] = meta
            save(conv_id, data)
    return p


def add_item(conv_id: str, section: str, text: str) -> bool:
    """向某节追加一条提炼知识；去重。返回是否新增。"""
    if section not in SECTIONS:
        section = "已确认事实"
    item = _clean_item(text)
    if not item:
        return False
    data = load(conv_id)
    sections = dict(data.get("sections") or {})
    items = list(sections.get(section) or [])
    if item in items:
        return False
    items.append(item)
    sections[section] = items
    data["sections"] = sections
    save(conv_id, data)
    return True


def set_title(conv_id: str, title: str) -> None:
    data = load(conv_id)
    meta = dict(data.get("meta") or {})
    meta["title"] = _clean_item(title)[:120]
    data["meta"] = meta
    save(conv_id, data)


def read_text(conv_id: str, max_bytes: int = 6000) -> str:
    """返回记忆文件的 Markdown 文本（截断）。"""
    p = path_for(conv_id)
    if not os.path.isfile(p):
        return ""
    try:
        with open(p, "r", encoding="utf-8") as f:
            text = f.read(max_bytes + 1)
    except Exception:
        return ""
    if len(text) > max_bytes:
        text = text[:max_bytes] + "\n…（记忆已截断）"
    return text


def list_convs() -> List[str]:
    _ensure_dir()
    out: List[str] = []
    try:
        for name in sorted(os.listdir(MEMORY_DIR)):
            if name.endswith(".md"):
                out.append(name[:-3])
    except Exception:
        pass
    return out


# ---------------------------------------------------------------------------
# 规则提炼：把一步动作+结果压缩成一条知识（不额外调用 API）
# ---------------------------------------------------------------------------

# 用户措辞 -> 偏好 的规则表（命中即记一条「用户偏好」）
PREFERENCE_RULES: List[tuple] = [
    (r"不要问确认|别问|不用确认|一步到底", "用户希望一步到底，不要反复确认"),
    (r"不要改|禁止|不许|别动", "用户明确禁止改动某些文件/模块，需严格遵守"),
    (r"最小改动|不要重构|别重构|最小化", "用户偏好最小改动，不要顺手重构"),
    (r"中文|用中文", "用户偏好使用中文交流"),
    (r"简洁|简短|别啰嗦|直接说", "用户偏好简洁直接的回复"),
    (r"先.*再.*验证|验证一下|确认生效", "用户要求改完必须验证"),
    (r"前端|页面|UI|界面", "用户关注前端/界面展示效果"),
]

# 从文件内容里识别项目架构的规则（命中即记一条「项目架构」）
ARCH_RULES: List[tuple] = [
    (r"FastAPI\(", "项目使用 FastAPI 框架"),
    (r"uvicorn\.run", "服务通过 uvicorn 启动"),
    (r"from fastapi import", "后端基于 FastAPI"),
    (r"@app\.(get|post|put|delete)", "存在 FastAPI 路由定义"),
    (r"sqlite3|SQLAlchemy|psycopg", "项目使用数据库"),
    (r"threading\.Thread", "使用线程执行后台任务"),
    (r"class .*Session", "存在会话管理类"),
    (r"def agent_loop", "存在 agent 主循环"),
    (r"import React|from react", "前端使用 React"),
    (r"<html", "包含 HTML 页面"),
]


def distill_prompt(prompt: str) -> List[tuple]:
    """从用户 prompt 提取「任务目标」和「用户偏好」。"""
    out: List[tuple] = []
    text = _clean_item(prompt or "")
    if not text:
        return out
    # 任务目标：取首句/前 120 字
    goal = re.split(r"[。\n]", text, maxsplit=1)[0].strip()
    if goal:
        out.append(("任务目标", goal[:120]))
    for pattern, desc in PREFERENCE_RULES:
        if re.search(pattern, text):
            out.append(("用户偏好", desc))
    return out


def distill_file(path: str, content: str) -> List[tuple]:
    """从 read_file 的路径/内容提取「项目架构」。"""
    out: List[tuple] = []
    p = _clean_item(path or "")
    if p:
        out.append(("项目架构", "涉及文件: " + p))
    body = content or ""
    if body:
        for pattern, desc in ARCH_RULES:
            if re.search(pattern, body):
                out.append(("项目架构", desc))
    return out


def distill_step(action: Dict[str, object], result: Dict[str, object]) -> List[tuple]:
    """返回 [(section, text), ...]。"""
    out: List[tuple] = []
    if not isinstance(action, dict) or not isinstance(result, dict):
        return out
    name = action.get("action")

    if name == "shell":
        cmd = _clean_item(str(action.get("command", "")))
        if result.get("error"):
            out.append(("未完成事项", "shell 失败: " + cmd + " -> " + _clean_item(str(result.get("error")))))
        else:
            code = result.get("exit_code")
            out.append(("已确认事实", "执行 shell: " + cmd + " (exit=" + str(code) + ")"))
    elif name == "write_file":
        p = str(result.get("path") or action.get("path") or "")
        if result.get("error"):
            out.append(("未完成事项", "写文件失败: " + p + " -> " + _clean_item(str(result.get("error")))))
        else:
            out.append(("已确认事实", "写入文件: " + p + " (" + str(result.get("bytes_written", "?")) + " bytes)"))
    elif name == "read_file":
        p = str(result.get("path") or action.get("path") or "")
        if result.get("error"):
            out.append(("未完成事项", "读文件失败: " + p + " -> " + _clean_item(str(result.get("error")))))
        else:
            out.append(("已确认事实", "读取文件: " + p + " (" + str(result.get("bytes_read", "?")) + " bytes)"))
            out.extend(distill_file(p, str(result.get("content") or "")))
    elif name == "self_restart":
        out.append(("关键决策", "执行 self_restart: " + _clean_item(str(action.get("reason", "")))))
    return out


def record_step(conv_id: str, action: Dict[str, object], result: Dict[str, object]) -> None:
    try:
        for section, text in distill_step(action, result):
            add_item(conv_id, section, text)
    except Exception:
        pass
