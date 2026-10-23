import json
import re
from typing import Any

import httpx

from app import config


class DeepSeekError(Exception):
    pass


def _extract_json(text: str) -> dict[str, Any]:
    """从模型回复中提取第一个 JSON 对象。"""
    text = text.strip()
    # 去掉可能的 markdown 代码围栏
    fence = re.search(r"```(?:json)?\s*(.*?)```", text, re.DOTALL)
    if fence:
        text = fence.group(1).strip()

    try:
        obj = json.loads(text)
        if isinstance(obj, dict):
            return obj
    except json.JSONDecodeError:
        pass

    # 退而求其次：扫描第一个平衡的 {...}
    start = text.find("{")
    if start != -1:
        depth = 0
        in_str = False
        esc = False
        for i in range(start, len(text)):
            ch = text[i]
            if in_str:
                if esc:
                    esc = False
                elif ch == "\\":
                    esc = True
                elif ch == '"':
                    in_str = False
                continue
            if ch == '"':
                in_str = True
            elif ch == "{":
                depth += 1
            elif ch == "}":
                depth -= 1
                if depth == 0:
                    candidate = text[start : i + 1]
                    try:
                        obj = json.loads(candidate)
                        if isinstance(obj, dict):
                            return obj
                    except json.JSONDecodeError:
                        break
    raise DeepSeekError("模型回复中未找到合法 JSON 动作")


def _redact(text: str) -> str:
    """对疑似密钥值脱敏，避免进入日志。"""
    if not text:
        return text
    for secret in (config.DEEPSEEK_API_KEY, config.AGENT_WEB_TOKEN):
        if secret and len(secret) >= 6:
            text = text.replace(secret, "***REDACTED***")
    # 通用 sk- 前缀密钥
    text = re.sub(r"sk-[A-Za-z0-9_\-]{8,}", "***REDACTED***", text)
    return text


def _complete(messages: list[dict[str, str]], *, json_mode: bool = False) -> str:
    """调用 DeepSeek chat completions，返回原始文本并记录用量。

    动作调用启用 JSON Output，减少模型输出解释文字或 markdown 围栏导致的解析失败。
    """
    url = config.DEEPSEEK_BASE_URL.rstrip("/") + "/chat/completions"
    headers = {
        "Authorization": f"Bearer {config.DEEPSEEK_API_KEY}",
        "Content-Type": "application/json",
    }
    payload = {
        "model": config.DEEPSEEK_MODEL,
        "messages": messages,
        "temperature": 0.2,
        "stream": False,
    }
    if json_mode:
        payload["response_format"] = {"type": "json_object"}

    try:
        with httpx.Client(timeout=config.AGENT_CMD_TIMEOUT) as client:
            resp = client.post(url, headers=headers, json=payload)
    except httpx.HTTPError as exc:
        raise DeepSeekError(f"请求 DeepSeek 失败: {_redact(str(exc))}") from exc

    if resp.status_code != 200:
        # 兼容不支持 response_format 的 OpenAI 兼容端点：退回普通文本模式重试一次。
        if json_mode and resp.status_code in (400, 422):
            return _complete(messages, json_mode=False)
        body = _redact(resp.text[:1000])
        raise DeepSeekError(f"DeepSeek 返回 HTTP {resp.status_code}: {body}")

    try:
        data = resp.json()
        content = data["choices"][0]["message"]["content"]
    except (KeyError, IndexError, ValueError) as exc:
        raise DeepSeekError(f"DeepSeek 响应格式异常: {_redact(str(exc))}") from exc

    # 记录 token 消耗（失败不影响主流程）
    try:
        from app import usage as _usage
        _conv = ""
        try:
            from app import session as _session
            _conv = _session.get_conv_id()
        except Exception:
            _conv = ""
        _usage.record(data.get("usage"), conv_id=_conv, source="chat")
    except Exception:
        pass

    if not isinstance(content, str):
        raise DeepSeekError("DeepSeek 返回内容不是字符串")
    return content


def chat_text(messages: list[dict[str, str]]) -> str:
    """闲聊/解释模式：返回原始文本，不尝试解析 JSON。"""
    return _complete(messages)


def chat(messages: list[dict[str, str]]) -> dict[str, Any]:
    """动作模式：启用 JSON Output，并提取单个 JSON 动作。"""
    raw = _complete(messages, json_mode=True)
    # DeepSeek 文档提示 JSON Output 偶尔可能返回空 content，退回普通模式再试一次。
    if not raw.strip():
        raw = _complete(messages, json_mode=False)
    return _extract_json(raw)
