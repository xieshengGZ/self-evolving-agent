"""Prompt classification with plugin hook + rule fallback.

The `classifier` plugin is preferred. If it is disabled, missing, or throws,
these rules keep the chat/task split working with zero extra API cost.
"""
import re
from typing import Any

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

QUESTION_PATTERNS = [
    r"是什么", r"什么是", r"为什么", r"为啥", r"为何", r"啥是", r"啥意思",
    r"有什么区别", r"区别", r"不同", r"怎么(?:回|样|回事|理解|用|工作|实现)",
    r"如何(?:理解|用|工作|实现)", r"解释", r"介绍", r"讲讲", r"讲一下",
    r"说说", r"科普", r"原理", r"what\s+is", r"what's", r"difference\s+between",
    r"how\s+does",
]

EXPLICIT_TASK_PATTERNS = [
    r"^(?:请|帮我|帮忙|麻烦|给我|替我|请你)\s*(?:把|将)?.{0,40}(?:写|创建|生成|修改|删除|运行|执行|安装|部署|提交|推送|查看|读取|分析|检查|修复|实现|优化|重构|新增|添加|更新|配置|启动|停止|重启)",
    r"^\s*(?:把|将).{0,40}(?:写|创建|生成|修改|删除|运行|执行|安装|部署|提交|推送|查看|读取|分析|检查|修复|实现|优化|重构|新增|添加|更新|配置|启动|停止|重启)",
    r"^\s*(?:写|创建|生成|修改|删除|运行|执行|安装|部署|提交|推送|查看|读取|分析|检查|修复|实现|优化|重构|新增|添加|更新|配置|启动|停止|重启)",
]

TASK_VERBS = [
    "执行", "运行", "创建", "修改", "删除", "部署", "安装", "读取",
    "查询", "启动", "停止", "重启", "编译", "测试", "提交", "推送",
    "写", "读", "看", "查", "改", "跑", "列", "装", "卸",
    "git", "docker", "systemctl", "pip", "apt", "npm", "curl", "wget",
]


def _match(patterns, text: str) -> bool:
    return any(re.match(pattern, text, re.I) for pattern in patterns)


def _search(patterns, text: str) -> bool:
    return any(re.search(pattern, text, re.I) for pattern in patterns)


def is_explanatory(text: Any) -> bool:
    """判断概念/解释类问题；显式“帮我写/查看/修复”等动作不算。"""
    if not isinstance(text, str):
        return False
    value = text.strip()
    if not value or _match(EXPLICIT_TASK_PATTERNS, value):
        return False
    return _search(QUESTION_PATTERNS, value)


def _rules(text: Any) -> str:
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
    if _match(EXPLICIT_TASK_PATTERNS, value):
        return "task"
    if is_explanatory(value):
        return "chat"
    low = value.lower()
    if any(verb in low for verb in TASK_VERBS):
        return "task"
    return "task"


def classify(text: Any) -> str:
    try:
        from app.kernel import get_runtime

        for result in get_runtime().emit_hook("classify.prompt", text=text):
            if isinstance(result, str) and result in MODES:
                if result == "task" and is_explanatory(text):
                    return "chat"
                return result
    except Exception:  # noqa: BLE001
        pass
    return _rules(text)
