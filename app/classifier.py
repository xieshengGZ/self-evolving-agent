"""Prompt routing with plugin hook + one conservative rule set.

Design rules:
- Default to `chat`; only explicit actions, continuations, or control commands
  are allowed to start the agent loop.
- Concept/question wording wins over incidental action words ("docker ... 什么意思").
- This module is the single source of truth; the classifier plugin delegates here.
"""
from __future__ import annotations

import re
from typing import Any, Dict

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
    r"[什么啥]意思", r"含义", r"意味着", r"what\s+does\s+.*\s+mean",
    r"什么时候", r"何时", r"哪里", r"哪个", r"哪种", r"谁", r"多少",
    r"是否", r"能否", r"可否", r"会不会", r"有没有", r"要不要", r"是不是",
    r"对不对", r"行不行", r"可不可以", r"有什么区别", r"区别", r"不同",
    r"怎么(?:回|样|回事|理解|用|工作|实现)", r"如何(?:理解|用|工作|实现)",
    r"解释", r"介绍", r"讲讲", r"讲一下", r"说说", r"科普", r"原理",
    r"what\s+is", r"what's", r"difference\s+between", r"how\s+does",
]

# Strong action verbs. Keep this list about imperative actions, not nouns that
# merely appear inside questions (e.g. "部署失败").
ACTION_VERBS = (
    "写|创建|生成|修改|删除|运行|执行|安装|部署|提交|推送|查看|查|读取|读|"
    "分析|检查|修复|实现|优化|重构|新增|添加|更新|配置|启动|停止|重启|编译|"
    "测试|清理|下载|上传|复制|移动|列出|列|打开|关闭|跑|装|卸|删|改|看"
)
TOOL_PREFIXES = (
    "git|docker|docker-compose|systemctl|pip|pip3|apt|apt-get|npm|pnpm|yarn|"
    "curl|wget|python|python3|bash|sh|make|ls|cat|grep|find|sed|awk"
)
POLITE_PREFIX = r"(?:请|帮我|帮忙|麻烦|给我|替我|请你|能帮我|可以帮我|能否帮我)"

# "帮我查一下 README" / "把这个文件删了"
POLITE_TASK_PATTERNS = [
    rf"^{POLITE_PREFIX}\s*(?:把|将)?.{{0,40}}(?:{ACTION_VERBS})",
]
BA_TASK_PATTERNS = [
    rf"^\s*(?:把|将).{{0,40}}(?:{ACTION_VERBS})",
]
# "查看 app/main.py" / "部署服务"
DIRECT_TASK_PATTERNS = [
    rf"^\s*(?:{ACTION_VERBS})",
]
# "git status" / "docker compose ps"
COMMAND_TASK_PATTERNS = [
    rf"^\s*(?:{TOOL_PREFIXES})\b",
]
# Requests about this codebase need tools even when phrased as a question.
CODEBASE_TASK_PATTERNS = [
    r"^(?:请|帮我|帮忙|麻烦|你能|能否)?\s*(?:分析|审查|评估|检查|看看|看一下|review)\s*.{0,30}(?:架构|代码|项目|仓库|模块|实现|设计|问题|改进)",
    r"(?:你的|你们的|这个|当前|本|我们|咱们)(?:项目|代码库|仓库|系统|agent|架构|代码|源码|模块|实现|设计).{0,40}(?:架构|结构|模块|实现|设计|问题|改进|优化|不足|风险|怎么样|如何)",
    r"(?:架构|代码|项目|仓库|模块|实现|设计).{0,20}(?:有哪些|有什么|哪里).{0,20}(?:改进|问题|优化|不足|风险)",
]

# Used by `is_explanatory`: a bare tool name ("git 是什么") is not an action
# request, so questions containing it still route to chat.
ACTION_TASK_PATTERNS = POLITE_TASK_PATTERNS + BA_TASK_PATTERNS + DIRECT_TASK_PATTERNS
TASK_PATTERNS = ACTION_TASK_PATTERNS + COMMAND_TASK_PATTERNS + CODEBASE_TASK_PATTERNS


def _match(patterns, text: str) -> bool:
    return any(re.match(pattern, text, re.I) for pattern in patterns)


def _search(patterns, text: str) -> bool:
    return any(re.search(pattern, text, re.I) for pattern in patterns)


def is_explanatory(text: Any) -> bool:
    """概念/解释类问题；显式“帮我写/查看/修复”等动作不算。"""
    if not isinstance(text, str):
        return False
    value = text.strip()
    if not value or _match(ACTION_TASK_PATTERNS, value):
        return False
    if _match(CODEBASE_TASK_PATTERNS, value):
        return False
    return _search(QUESTION_PATTERNS, value)


def _rules_with_reason(text: Any) -> Dict[str, str]:
    if not isinstance(text, str):
        return {"mode": "chat", "source": "rules", "reason": "invalid-input"}
    value = text.strip()
    if not value:
        return {"mode": "chat", "source": "rules", "reason": "empty"}
    if _match(CONTROL_PATTERNS, value):
        return {"mode": "control", "source": "rules", "reason": "control-command"}
    if _match(CONTINUATION_PATTERNS, value):
        return {"mode": "continuation", "source": "rules", "reason": "continuation"}
    if _match(CHAT_PATTERNS, value):
        return {"mode": "chat", "source": "rules", "reason": "greeting"}
    if _match(CODEBASE_TASK_PATTERNS, value):
        return {"mode": "task", "source": "rules", "reason": "codebase-analysis"}
    if is_explanatory(value):
        return {"mode": "chat", "source": "rules", "reason": "question-pattern"}
    if _match(TASK_PATTERNS, value):
        return {"mode": "task", "source": "rules", "reason": "explicit-action"}
    return {"mode": "chat", "source": "rules", "reason": "default-chat"}


def classify_rules(text: Any) -> str:
    """Pure rule classification; the plugin uses this without re-entering hooks."""
    return _rules_with_reason(text)["mode"]


def _rules(text: Any) -> str:
    return classify_rules(text)


def explain(text: Any) -> Dict[str, str]:
    """Return mode/source/reason for logs; defaults to chat on uncertainty."""
    try:
        from app.kernel import get_runtime

        for result in get_runtime().emit_hook("classify.prompt", text=text):
            if isinstance(result, str) and result in MODES:
                if result == "task" and is_explanatory(text):
                    return {
                        "mode": "chat",
                        "source": "plugin",
                        "reason": "plugin-task-overridden-by-question",
                    }
                return {"mode": result, "source": "plugin", "reason": "classify.prompt"}
    except Exception:  # noqa: BLE001
        pass
    return _rules_with_reason(text)


def classify(text: Any) -> str:
    return explain(text)["mode"]
