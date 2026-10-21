import importlib.util
from pathlib import Path
from types import SimpleNamespace

from app.kernel.registry import ContextRegistry

ROOT = Path(__file__).resolve().parents[1]


def load_module(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


class FakeStore:
    def __init__(self):
        self.state = {"plan_text": "1. [x] done"}
        self.history = [
            {"role": "user", "content": "hello"},
            {"role": "assistant", "content": "hi"},
        ]

    def get_task(self):
        return "demo task"

    def get_task_state(self):
        return dict(self.state)

    def get_memory_text(self, max_bytes=6000):
        return "memory fact"

    def get_history(self):
        return list(self.history)


def test_context_plugins_register_all_sections():
    registry = ContextRegistry()
    configs = {
        "context_base": {},
        "context_plan": {},
        "context_memory": {},
        "context_window": {},
    }
    for name, config in configs.items():
        module = load_module(name + "_plugin", ROOT / "plugins" / name / "plugin.py")
        module.setup(SimpleNamespace(context=registry, config=config))
    entries = registry.entries()
    assert len(entries) == 5
    store = FakeStore()
    sections = []
    for _order, builder in entries:
        sections.extend(builder(store, "SYS"))
    kinds = [item["kind"] for item in sections]
    assert kinds.count("history") == 2
    assert {"system", "task", "plan", "memory"}.issubset(kinds)
    assert sections[0]["required"] is True


def test_phase3_manifests_are_valid():
    import json

    for name in ("context_base", "context_plan", "context_memory", "context_window"):
        data = json.loads((ROOT / "plugins" / name / "plugin.json").read_text(encoding="utf-8"))
        assert data["name"] == name
        assert data["api_version"] == 1
        assert data["status"] == "active"
        assert (ROOT / "plugins" / name / data["entry"]).is_file()


def test_runtime_uses_context_base_builders():
    from app import context
    from app.kernel.runtime import KernelRuntime

    runtime = KernelRuntime(plugins_dir=ROOT / "plugins")
    runtime.start(announce=False)
    try:
        builders = runtime.context_builders()
        assert any(name == "context_base" for _, name, _ in builders)
        built = context.build("SYS", runtime=runtime)
        assert built["ledger"]["fallback"] is False
        assert built["messages"][0] == {"role": "system", "content": "SYS"}
        assert built["ledger"]["sections"][0]["source"] == "context_base"
    finally:
        runtime.stop()
