import pytest

from app import classifier


CHAT_CASES = [
    "docker compose exec -T app 什么意思",
    "什么意思",
    "今天星期几",
    "为什么部署失败",
    "git 是什么",
    "怎么部署",
    "文件在哪",
    "你好",
    "介绍一下你自己",
    "解释一下 SSE",
    "今天天气怎么样",
    "什么是 Docker",
]

TASK_CASES = [
    "查看 app/main.py",
    "运行 date",
    "部署服务",
    "docker compose ps",
    "能帮我查一下 README 吗",
    "帮我写 SSE 文档",
    "把文件删了",
    "删除 /tmp/x",
    "git status",
    "修改 config.py",
]


@pytest.mark.parametrize("prompt", CHAT_CASES)
def test_question_and_default_prompts_stay_in_chat(prompt):
    assert classifier.classify(prompt) == "chat"


@pytest.mark.parametrize("prompt", TASK_CASES)
def test_explicit_actions_route_to_task(prompt):
    assert classifier.classify(prompt) == "task"


def test_control_and_continuation_are_not_tasks():
    assert classifier.classify("停止") == "control"
    assert classifier.classify("继续") == "continuation"
    assert classifier.classify("开始") == "continuation"
    assert classifier.classify("要") == "continuation"
    assert classifier.classify("好的") == "continuation"


def test_imperative_with_prefix_is_a_task():
    assert classifier.classify("先修 1（影响用户体验，改动最小）。改动前先提交github。") == "task"


def test_default_is_chat_with_reason():
    route = classifier.explain("今天星期几")
    assert route == {"mode": "chat", "source": "rules", "reason": "default-chat"}


def test_codebase_analysis_question_needs_tools():
    route = classifier.explain("你的架构是怎么样的。有哪些可以改进的？")
    assert route["mode"] == "task"
    assert route["reason"] == "codebase-analysis"


def test_question_wins_over_incidental_tool_name():
    route = classifier.explain("docker compose exec -T app 什么意思")
    assert route["mode"] == "chat"
    assert route["reason"] == "question-pattern"
