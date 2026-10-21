import json
from types import SimpleNamespace

from app import context


class FakeSession:
    def __init__(self, task="", preview=None):
        self.task = task
        self.preview = preview or {"parts": []}

    def get_task(self):
        return self.task

    def get_conv_id(self):
        return "conv-context"

    def build_context_preview(self, system_prompt):
        return self.preview


def section(
    kind,
    content,
    *,
    role="system",
    priority=100,
    order=100,
    required=False,
    compressible=True,
):
    return {
        "kind": kind,
        "role": role,
        "content": content,
        "priority": priority,
        "order": order,
        "required": required,
        "compressible": compressible,
    }


def test_estimate_tokens_is_conservative_for_cjk():
    assert context.estimate_tokens("") == 0
    assert context.estimate_tokens("abcd") == 1
    assert context.estimate_tokens("你好世界") == 4
    assert context.estimate_tokens("a" * 400) == 100


def test_budget_keeps_required_and_newest_history(monkeypatch):
    monkeypatch.setattr(context, "session", FakeSession())
    monkeypatch.setattr(context.config, "AGENT_CONTEXT_BUDGET", 300)
    monkeypatch.setattr(context.config, "AGENT_CONTEXT_PRESSURE", 0.7)

    def builder(store, system_prompt):
        return [
            section("system", "S" * 900, priority=1000, order=0, required=True),
            section("task", "T" * 100, role="user", priority=900, order=10, required=True),
            section(
                "history",
                "old " + "a" * 2000,
                role="user",
                priority=500,
                order=100,
                compressible=False,
            ),
            section("history", "new " + "b" * 20, role="user", priority=501, order=101),
        ]

    runtime = SimpleNamespace(
        store=object(),
        context_builders=lambda: [(10, "fake", builder)],
        emit_hook=lambda *args, **kwargs: [],
    )
    built = context.build("SYS", runtime=runtime)
    text = "\n".join(message["content"] for message in built["messages"])
    assert "S" * 100 in text
    assert "T" * 20 in text
    assert "new" in text
    assert built["ledger"]["compressed"] is True
    assert built["ledger"]["omitted_sections"] == 1
    assert built["ledger"]["pressure"] is True
    assert built["ledger"]["used_tokens"] <= built["ledger"]["budget_tokens"]


def test_compress_hook_can_replace_sections(monkeypatch):
    monkeypatch.setattr(context, "session", FakeSession())
    monkeypatch.setattr(context.config, "AGENT_CONTEXT_BUDGET", 300)

    def builder(store, system_prompt):
        return [
            section("system", "SYS", priority=1000, order=0, required=True),
            section("history", "x" * 2000, role="user", priority=500, order=100),
        ]

    def compress(**kwargs):
        return [[
            section("system", "SYS", priority=1000, order=0, required=True),
            section("history", "compressed", role="user", priority=500, order=100),
        ]]

    runtime = SimpleNamespace(
        store=object(),
        context_builders=lambda: [(10, "fake", builder)],
        emit_hook=lambda event, **kwargs: compress(**kwargs) if event == "context.compress" else [],
    )
    built = context.build("SYS", runtime=runtime)
    assert built["ledger"]["compressed"] is True
    assert built["messages"] == [
        {"role": "system", "content": "SYS"},
        {"role": "user", "content": "compressed"},
    ]


def test_fallback_uses_legacy_preview(monkeypatch):
    fake = FakeSession(
        preview={
            "parts": [
                {"kind": "system_prompt", "role": "system", "content": "SYS", "label": "system"},
                {"kind": "history", "role": "user", "content": "hello", "label": "history"},
            ]
        }
    )
    monkeypatch.setattr(context, "session", fake)
    built = context.build("SYS", runtime=None)
    assert built["ledger"]["fallback"] is True
    assert built["messages"][0] == {"role": "system", "content": "SYS"}
    assert built["messages"][-1] == {"role": "user", "content": "hello"}


def test_large_result_is_offloaded_to_artifact(monkeypatch, tmp_path):
    state = tmp_path / "state"
    monkeypatch.setattr(context.config, "STATE_DIR", str(state))
    monkeypatch.setattr(context.config, "AGENT_ROOT", str(tmp_path))
    monkeypatch.setattr(context.config, "AGENT_ARTIFACT_OFFLOAD", 1)
    monkeypatch.setattr(context.config, "AGENT_ARTIFACT_THRESHOLD", 100)
    monkeypatch.setattr(context.session, "get_conv_id", lambda: "conv-artifact")

    result = {"result_of": "shell", "exit_code": 0, "stdout": "x" * 500}
    summary, artifact = context.offload_result({"action": "shell"}, result)
    assert artifact is not None
    assert summary["context_truncated"] is True
    assert summary["artifact"] == artifact["path"]
    assert summary["stdout_preview"].endswith("x" * 100)
    stored = json.loads((tmp_path / artifact["rel"]).read_text(encoding="utf-8"))
    assert stored["stdout"] == result["stdout"]

    same, escaped = context.offload_result(
        {"action": "shell"}, result, conv_id=".."
    )
    assert same is not result
    assert escaped is not None
    assert escaped["path"].startswith(str(state / "artifacts") + "/")

    small = {"result_of": "shell", "exit_code": 0}
    same, meta = context.offload_result({"action": "shell"}, small)
    assert same is small
    assert meta is None


def test_fallback_system_survives_tiny_budget(monkeypatch):
    system = "S" * 4000
    fake = FakeSession(
        preview={
            "parts": [
                {"kind": "system", "role": "system", "content": system, "label": "system"},
                {"kind": "history", "role": "user", "content": "old " * 100, "label": "history"},
            ]
        }
    )
    monkeypatch.setattr(context, "session", fake)
    monkeypatch.setattr(context.config, "AGENT_CONTEXT_BUDGET", 256)
    built = context.build(system, runtime=None)
    assert built["messages"] == [{"role": "system", "content": system}]
    assert built["ledger"]["over_budget"] is True
    assert built["ledger"]["omitted_sections"] == 1


def test_compress_hook_runs_at_pressure_before_budget(monkeypatch):
    monkeypatch.setattr(context, "session", FakeSession())
    monkeypatch.setattr(context.config, "AGENT_CONTEXT_BUDGET", 1000)
    calls = []

    def builder(store, system_prompt):
        return [
            section("system", "SYS", priority=1000, order=0, required=True),
            section("history", "x" * 3000, role="user", priority=500, order=100),
        ]

    def compress(**kwargs):
        calls.append(kwargs["used"])
        return [[
            section("system", "SYS", priority=1000, order=0, required=True),
            section("history", "compressed", role="user", priority=500, order=100),
        ]]

    runtime = SimpleNamespace(
        store=object(),
        context_builders=lambda: [(10, "fake", builder)],
        emit_hook=lambda event, **kwargs: compress(**kwargs) if event == "context.compress" else [],
    )
    built = context.build("SYS", runtime=runtime)
    assert len(calls) == 1
    assert built["ledger"]["compressed"] is True
    assert built["ledger"]["over_budget"] is False
    assert built["ledger"]["used_tokens"] < built["ledger"]["budget_tokens"]
