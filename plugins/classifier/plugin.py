"""Prompt classifier plugin.

Uses the existing hook bus instead of a new registry: the kernel asks
`classify.prompt`, and the first valid mode wins. If this plugin is absent or
fails, `app.classifier` keeps a rule-based fallback.
"""
from __future__ import annotations

import re
from typing import Any, Dict

from app.kernel.api import PluginContext

MODES = {"chat", "task", "continuation", "control"}

CONTROL_PATTERNS = [
    r"^(停止|停下|停|取消|中断|终止|别做了|stop|cancel|abort)$",
    r"^(状态|进度|进展|现在什么状态|当前状态|status)$",
    r"^(重启|重新启动|restart|reload)$",
]

CONTINUATION_PATTERNS = [
    r"^(继续|接着|往下|下一步|然后呢|go\s*on|continue|resume)",
    r"^(接着做|继续做|继续执行|继续刚才)",
]

CHAT_PATTERNS = [
    r"^(你好|hi|hello|在吗|嗨|哈喽|早上好|晚上好)[\s!！。？?]*$",
    r"^(你是谁|你叫什么|你能做什么|介绍一下你自己|你能干啥)[\s?？。]*$",
    r"^(谢谢|thanks|thx|好的|ok|嗯|收到|明白)[\s!！。]*$",
    r"^(再见|拜拜|bye|晚安)[\s!！。]*$",
]

TASK_VERBS = [
    "执行", "运行", "创建", "修改", "删除", "部署", "安装", "读取",
    "查询", "启动", "停止", "重启", "编译", "测试", "提交", "推送",
    "写", "读", "看", "查", "改", "跑", "列", "装", "卸",
    "git", "docker", "systemctl", "pip", "apt", "npm", "curl", "wget",
]


def _match(patterns, text: str) -> bool:
    return any(re.match(pattern, text, re.I) for pattern in patterns)


def classify(text: Any) -> str:
    if not isinstance(text, str):
        return "task"
    value = text.strip()
    if not value:
        return "chat"
    if _match(CONTROL_PATTERNS, value):
        return "control"
    if _match(CONTINUATION_PATTERNS, value):
        return "continuation"
    if _match(CHAT_PATTERNS, value):
        return "chat"
    low = value.lower()
    if any(verb in low for verb in TASK_VERBS):
        return "task"
    return "task"


def _on_classify(**kwargs: Any) -> str:
    return classify(kwargs.get("text", ""))


def setup(ctx: PluginContext) -> None:
    ctx.hooks.on("classify.prompt", _on_classify)


def teardown(ctx: PluginContext) -> None:
    pass
