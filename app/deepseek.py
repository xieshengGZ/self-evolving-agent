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


def chat(messages: list[dict[str, str]]) -> dict[str, Any]:
    """调用 DeepSeek chat completions，返回解析后的 JSON 动作字典。"""
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

    try:
        with httpx.Client(timeout=config.AGENT_CMD_TIMEOUT) as client:
            resp = client.post(url, headers=headers, json=payload)
    except httpx.HTTPError as exc:
        raise DeepSeekError(f"请求 DeepSeek 失败: {_redact(str(exc))}") from exc

    if resp.status_code != 200:
        body = _redact(resp.text[:1000])
        raise DeepSeekError(f"DeepSeek 返回 HTTP {resp.status_code}: {body}")

    try:
        data = resp.json()
        content = data["choices"][0]["message"]["content"]
    except (KeyError, IndexError, ValueError) as exc:
        raise DeepSeekError(f"DeepSeek 响应格式异常: {_redact(str(exc))}") from exc

    if not isinstance(content, str):
        raise DeepSeekError("DeepSeek 返回内容不是字符串")

    return _extract_json(content)