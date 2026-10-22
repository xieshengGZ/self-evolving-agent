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
    assert any(event["kind"] == "done" for event in local_session.events)
    assert any(
        event["kind"] == "error" and "action" in event["message"]
        for event in local_session.events
    )
    assert any(
        event["kind"] == "error" and "未知 action" in event["message"]
        for event in local_session.events
    )
    assert db.get_plan(state["plan"]["id"])["status"] == "done"
