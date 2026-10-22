import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace

from app.kernel.bus import EventBus
from app.kernel.registry import HookBus, HttpRegistry, PlannerRegistry
from app.kernel.runtime import KernelRuntime
from app.kernel.store import Store

ROOT = Path(__file__).resolve().parents[1]


def load_module(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def test_real_phase2_plugins_load_and_register():
    runtime = KernelRuntime(plugins_dir=ROOT / "plugins")
    runtime.start(announce=False)
    try:
        assert runtime.get_planner("default") is not None
        assert "default" in runtime.plugins.planner_names()
        modes = runtime.emit_hook("classify.prompt", text="继续")
        assert "continuation" in modes
        modes = runtime.emit_hook("classify.prompt", text="停止")
        assert "control" in modes
    finally:
        runtime.stop()


def test_classifier_plugin_rules():
    module = load_module("classifier_plugin", ROOT / "plugins/classifier/plugin.py")
    hooks = HookBus()
    module.setup(SimpleNamespace(hooks=hooks))

    assert hooks.emit("classify.prompt", text="你好") == ["chat"]
    assert hooks.emit("classify.prompt", text="继续") == ["continuation"]
    assert hooks.emit("classify.prompt", text="停止") == ["control"]
    assert hooks.emit("classify.prompt", text="部署服务") == ["task"]
    assert hooks.emit(
        "classify.prompt",
        text="事件经 SSE 推前端、状态持久化到 SQLite。 sse是什么，sqlite是什么",
    ) == ["chat"]
    assert hooks.emit("classify.prompt", text="SSE 和 SQLite 有什么区别？") == ["chat"]
    assert hooks.emit("classify.prompt", text="为什么部署失败") == ["chat"]
    assert hooks.emit("classify.prompt", text="git 是什么") == ["chat"]
    assert hooks.emit(
        "classify.prompt",
        text="什么时候会有这么复杂的业务。到时候我使用子agent实现具体的业务流程不就行了吗",
    ) == ["chat"]


def test_classifier_keeps_concept_questions_out_of_task_mode():
    from app import classifier

    assert classifier.classify(
        "事件经 SSE 推前端、状态持久化到 SQLite。 sse是什么，sqlite是什么"
    ) == "chat"
    assert classifier.classify("解释一下 SSE 和 SQLite") == "chat"
    assert classifier.classify("为什么部署失败") == "chat"
    assert classifier.classify("git 是什么") == "chat"
    assert classifier.classify(
        "什么时候会有这么复杂的业务。到时候我使用子agent实现具体的业务流程不就行了吗"
    ) == "chat"
    assert classifier.classify("能帮我查一下 README 吗") == "task"
    assert classifier.is_explanatory("SSE 是什么") is True
    assert classifier.is_explanatory("帮我写 SSE 文档") is False
    assert classifier.classify("帮我把 SSE 和 SQLite 写成文档") == "task"
    assert classifier.classify("查看 app/main.py") == "task"


def test_classifier_downgrades_plugin_task_for_concept_question(monkeypatch):
    import app.classifier as classifier_mod

    class FakeRuntime:
        def emit_hook(self, event, **kwargs):
            return ["task"]

    monkeypatch.setattr("app.kernel.get_runtime", lambda: FakeRuntime())
    assert classifier_mod.classify("SSE 是什么") == "chat"
    assert classifier_mod.classify("查看 app/main.py") == "task"


def test_planning_plugin_create_and_reset():
    module = load_module("planning_plugin", ROOT / "plugins/planning/plugin.py")

    class FakeLLM:
        def chat(self, messages):
            if "失败/验收反馈" in messages[-1]["content"]:
                return {
                    "reasoning": "retry",
                    "acceptance": [{"kind": "file_exists", "path": "/tmp/x"}],
                    "steps": [{"id": 1, "action": "shell", "expected": "retry"}],
                }
            return {
                "reasoning": "do it",
                "acceptance": [{"kind": "file_exists", "path": "/tmp/x"}],
                "steps": [{"id": 1, "action": "shell", "expected": "write"}],
            }

    class FakeStore:
        def __init__(self):
            self.state = {}
            self.cancelled = []

        def get_task_state(self):
            return dict(self.state)

        def set_task_state(self, state):
            self.state = dict(state)

        def update_plan(self, plan_id, **fields):
            self.cancelled.append((plan_id, fields))

    ctx = SimpleNamespace(
        config={"max_steps": 8},
        planners=PlannerRegistry(),
        http=HttpRegistry(),
        hooks=HookBus(),
        store=FakeStore(),
        llm=FakeLLM(),
    )
    module.setup(ctx)
    planner = ctx.planners.get("default")
    assert planner is not None

    result = planner.create("goal", actions=["shell"])
    assert result["steps"][0]["action"] == "shell"
    assert result["acceptance"][0]["kind"] == "file_exists"

    revised = planner.revise(
        {"goal": "goal", "steps": [{"id": 1, "action": "shell"}]},
        "failed",
        actions=["shell"],
    )
    assert revised["steps"][0]["expected"] == "retry"

    ctx.store.state = {"plan": {"id": 7}, "plan_text": "x"}
    assert ctx.http.get("status")({})["plan"]["id"] == 7
    assert ctx.http.get("reset")({})["ok"] is True
    assert "plan" not in ctx.store.state
    assert ctx.store.cancelled == [(7, {"status": "cancelled"})]


def test_phase2_manifests_are_valid():
    for name in ("planning", "classifier"):
        data = json.loads((ROOT / "plugins" / name / "plugin.json").read_text(encoding="utf-8"))
        assert data["name"] == name
        assert data["api_version"] == 1
        assert data["status"] == "active"
        assert (ROOT / "plugins" / name / data["entry"]).is_file()
