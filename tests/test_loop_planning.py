from types import SimpleNamespace

from app import planning
from app.kernel import loop as loop_mod
from app.session import Session
from app.storage import Storage


class FakePlanner:
    def create(self, task, actions=None):
        return {
            "reasoning": "one step",
            "acceptance": [{"kind": "file_exists", "path": self.target}],
            "steps": [
                {
                    "id": 1,
                    "action": "shell",
                    "args": {"command": "true"},
                    "expected": "run true",
                }
            ],
        }

    def revise(self, plan, feedback, actions=None):
        return self.create(plan["goal"], actions)


class FakeRuntime:
    def __init__(self, planner):
        self.plugins = SimpleNamespace(
            get_planner=lambda name="default": planner,
            tool_names=lambda: ["shell"],
        )
        self.executed = []

    def execute_tool(self, action):
        self.executed.append(action)
        return {"result_of": "shell", "exit_code": 0, "stdout": "ok"}


def test_agent_loop_creates_executes_and_verifies_plan(monkeypatch, tmp_path):
    target = tmp_path / "done.txt"
    target.write_text("ok", encoding="utf-8")

    artifact = tmp_path / "agent.db"
    db = Storage(artifact)
    db.init()
    local_session = Session(storage_backend=db, auto_migrate=False)
    local_session.load()
    local_session.set_task("run a tiny task")
    local_session.set_status("running")

    planner = FakePlanner()
    planner.target = str(target)
    runtime = FakeRuntime(planner)

    replies = iter(
        [
            {"kind": "assistant", "message": "not an action"},
            {"action": "nope"},
            {"action": "shell", "command": "true"},
            {"action": "done", "message": "finished"},
        ]
    )

    monkeypatch.setattr(loop_mod, "session", local_session)
    monkeypatch.setattr(planning, "session", local_session)
    monkeypatch.setattr(planning, "_audit_storage", lambda: db)
    monkeypatch.setattr(loop_mod, "get_runtime", lambda: runtime)
    monkeypatch.setattr(loop_mod.deepseek, "chat", lambda messages: next(replies))
    monkeypatch.setattr(planning.config, "AGENT_MAX_STEPS", 4)

    loop_mod.agent_loop()

    state = local_session.get_task_state()
    assert state["plan"]["status"] == "done"
    assert state["plan"]["steps"][0]["status"] == "done"
    assert runtime.executed == [{"action": "shell", "command": "true"}]
    assert local_session.get_status() == "idle"
    assert local_session.get_last_stop()["reason"] == "done"
    assert any(event["kind"] == "done" for event in local_session.events)
    assert local_session.events[-1]["kind"] == "status"
    assert "完成" in local_session.events[-1]["message"]
    assert any(
        event["kind"] == "error" and "action" in event["message"]
        for event in local_session.events
    )
    assert any(
        event["kind"] == "error" and "未知 action" in event["message"]
        for event in local_session.events
    )
    assert db.get_plan(state["plan"]["id"])["status"] == "done"


class EmptyPlanner:
    def create(self, task, actions=None):
        raise RuntimeError("empty plan")

    def revise(self, plan, feedback, actions=None):
        raise RuntimeError("empty plan")


def _local_session(tmp_path, task="run a tiny task"):
    db = Storage(tmp_path / "agent.db")
    db.init()
    local_session = Session(storage_backend=db, auto_migrate=False)
    local_session.load()
    local_session.set_task(task)
    local_session.set_status("running")
    return local_session, db


def test_empty_plan_falls_back_to_chat(monkeypatch, tmp_path):
    local_session, db = _local_session(tmp_path, "docker compose exec -T app 什么意思")
    runtime = FakeRuntime(EmptyPlanner())

    monkeypatch.setattr(loop_mod, "session", local_session)
    monkeypatch.setattr(planning, "session", local_session)
    monkeypatch.setattr(planning, "_audit_storage", lambda: db)
    monkeypatch.setattr(loop_mod, "get_runtime", lambda: runtime)
    monkeypatch.setattr(loop_mod.deepseek, "chat_text", lambda messages: "直接回答用户")
    monkeypatch.setattr(
        loop_mod.deepseek,
        "chat",
        lambda messages: (_ for _ in ()).throw(AssertionError("must not run loop")),
    )

    loop_mod.agent_loop()

    assert local_session.get_status() == "idle"
    assert local_session.get_last_stop()["reason"] == "chat_fallback"
    assert not any(event["kind"] == "info" and "step 1/" in event["message"] for event in local_session.events)
    assert local_session.history[-1] == {"role": "assistant", "content": "直接回答用户"}
    assert any(event["kind"] == "done" and event["message"] == "直接回答用户" for event in local_session.events)


def test_no_tool_failures_fall_back_to_chat(monkeypatch, tmp_path):
    local_session, db = _local_session(tmp_path)
    runtime = FakeRuntime(EmptyPlanner())

    monkeypatch.setattr(loop_mod, "session", local_session)
    monkeypatch.setattr(planning, "session", local_session)
    monkeypatch.setattr(planning, "_audit_storage", lambda: db)
    monkeypatch.setattr(loop_mod, "get_runtime", lambda: runtime)
    monkeypatch.setattr(loop_mod.config, "AGENT_ENABLE_PLANNER", 0)
    monkeypatch.setattr(loop_mod.config, "AGENT_MAX_CONSECUTIVE_FAILURES", 3)
    monkeypatch.setattr(
        loop_mod.deepseek,
        "chat",
        lambda messages: (_ for _ in ()).throw(loop_mod.deepseek.DeepSeekError("bad json")),
    )
    monkeypatch.setattr(loop_mod.deepseek, "chat_text", lambda messages: "兜底回答")

    loop_mod.agent_loop()

    assert local_session.get_status() == "idle"
    assert local_session.get_last_stop()["reason"] == "chat_fallback"
    assert local_session.history[-1] == {"role": "assistant", "content": "兜底回答"}


def test_explicit_task_mode_empty_plan_reports_task_mode(monkeypatch, tmp_path):
    local_session, db = _local_session(tmp_path, "你现在是什么模式？")
    runtime = FakeRuntime(EmptyPlanner())

    monkeypatch.setattr(loop_mod, "session", local_session)
    monkeypatch.setattr(planning, "session", local_session)
    monkeypatch.setattr(planning, "_audit_storage", lambda: db)
    monkeypatch.setattr(loop_mod, "get_runtime", lambda: runtime)
    monkeypatch.setattr(
        loop_mod.deepseek,
        "chat_text",
        lambda messages: (_ for _ in ()).throw(AssertionError("must not chat fallback")),
    )
    monkeypatch.setattr(
        loop_mod.deepseek,
        "chat",
        lambda messages: (_ for _ in ()).throw(AssertionError("must not run loop")),
    )

    loop_mod.agent_loop(task_mode=True)

    assert local_session.get_status() == "idle"
    assert local_session.get_last_stop()["reason"] == "task_no_plan"
    assert "当前是任务模式" in local_session.history[-1]["content"]
    assert "对话模式" not in local_session.history[-1]["content"]
    assert not any(
        event["kind"] == "warn" and "回退为聊天" in event["message"]
        for event in local_session.events
    )


def test_completed_plan_is_verified_without_extra_model_steps(monkeypatch, tmp_path):
    target = tmp_path / "done.txt"
    target.write_text("ok", encoding="utf-8")

    local_session, db = _local_session(tmp_path, "run a tiny task")
    planner = FakePlanner()
    planner.target = str(target)
    runtime = FakeRuntime(planner)
    calls = {"n": 0}

    def fake_chat(messages):
        calls["n"] += 1
        if calls["n"] > 1:
            raise AssertionError("model must not run after all plan steps are done")
        return {"action": "shell", "command": "true"}

    monkeypatch.setattr(loop_mod, "session", local_session)
    monkeypatch.setattr(planning, "session", local_session)
    monkeypatch.setattr(planning, "_audit_storage", lambda: db)
    monkeypatch.setattr(loop_mod, "get_runtime", lambda: runtime)
    monkeypatch.setattr(loop_mod.deepseek, "chat", fake_chat)

    loop_mod.agent_loop()

    assert calls["n"] == 1
    assert local_session.get_last_stop()["reason"] == "done"
    assert any(
        event["kind"] == "info" and "自动验收" in event["message"]
        for event in local_session.events
    )
