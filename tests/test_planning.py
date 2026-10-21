import json

import pytest

from app import planning
from app.planning import Plan, PlanStep
from app.storage import Storage


class FakeSession:
    def __init__(self, conv_id="conv-plan", task="goal"):
        self.conv_id = conv_id
        self.task = task
        self.state = {}

    def get_conv_id(self):
        return self.conv_id

    def get_task(self):
        return self.task

    def get_task_state(self):
        return dict(self.state)

    def set_task_state(self, state):
        self.state = dict(state)


def make_plan():
    return Plan(
        goal="create a file",
        steps=[
            PlanStep(id=1, action="shell", expected="write file", acceptance=[]),
            PlanStep(id=2, action="read_file", expected="read it back", acceptance=[]),
        ],
        status="planning",
        acceptance=[{"kind": "file_exists", "path": "/tmp/plan-test"}],
    )


def test_plan_state_machine_and_roundtrip():
    plan = make_plan()
    assert plan.transition("executing") is True
    assert plan.transition("done") is False
    assert plan.current_step().id == 1
    plan.steps[0].status = "done"
    assert plan.current_step().id == 2

    restored = Plan.from_dict(plan.to_dict())
    assert restored.goal == plan.goal
    assert restored.acceptance == plan.acceptance
    assert [step.id for step in restored.steps] == [1, 2]


def test_legacy_plan_list_is_supported():
    plan = Plan.from_dict(
        [{"id": 1, "action": "shell", "expected": "old"}],
        conv_id="legacy",
        goal="old task",
    )
    assert plan.status == "executing"
    assert plan.steps[0].expected == "old"
    assert plan.format_text().startswith("目标: old task")


def test_record_result_advances_and_marks_failure(monkeypatch):
    monkeypatch.setattr(planning, "save", lambda plan, audit=True: plan)
    plan = make_plan()
    plan.transition("executing")
    planning.before_step(plan)
    assert plan.steps[0].status == "in_progress"

    assert planning.record_result(plan, {"action": "shell"}, {"exit_code": 0}) == "done"
    assert plan.steps[0].status == "done"
    assert plan.current == 1

    assert planning.record_result(plan, {"action": "read_file"}, {"error": "nope"}) == "failed"
    assert plan.steps[1].status == "failed"


def test_verify_file_and_command_checks(monkeypatch, tmp_path):
    target = tmp_path / "artifact.txt"
    target.write_text("hello plan", encoding="utf-8")
    monkeypatch.setattr(planning, "save", lambda plan, audit=True: plan)

    plan = make_plan()
    plan.acceptance = [
        {"kind": "file_exists", "path": str(target)},
        {"kind": "file_not_empty", "path": str(target)},
        {"kind": "contains", "path": str(target), "text": "hello"},
        {"kind": "command", "command": f"cat {target}"},
    ]
    result = planning.verify_plan(plan)
    assert result["ok"] is True
    assert len(result["checks"]) == 4

    plan.acceptance.append(
        {"kind": "command", "command": "sh -c 'exit 7'", "expected_code": 0}
    )
    result = planning.verify_plan(plan)
    assert result["ok"] is False
    assert "command" in planning.verification_feedback(result)


def test_save_load_and_clear(monkeypatch, tmp_path):
    fake = FakeSession()
    db = Storage(tmp_path / "agent.db")
    db.init()
    monkeypatch.setattr(planning, "session", fake)
    monkeypatch.setattr(planning, "_audit_storage", lambda: db)

    plan = make_plan()
    plan.transition("executing")
    planning.save(plan)
    assert plan.id > 0
    assert fake.state["plan"]["goal"] == plan.goal
    assert "目标: create a file" in fake.state["plan_text"]

    loaded = planning.load_plan()
    assert loaded is not None
    assert loaded.id == plan.id

    planning.clear_plan("test reset")
    assert planning.load_plan() is None
    assert fake.state["plan_reset_reason"] == "test reset"
    assert db.get_plan(plan.id)["status"] == "cancelled"


def test_create_and_revise_with_fake_planner(monkeypatch):
    fake = FakeSession()
    monkeypatch.setattr(planning, "session", fake)
    monkeypatch.setattr(planning, "save", lambda plan, audit=True: plan)

    class FakePluginManager:
        def tool_names(self):
            return ["shell"]

        def get_planner(self, name="default"):
            return self

        def create(self, task, actions=None):
            return {
                "reasoning": "test",
                "acceptance": [{"kind": "file_exists", "path": "/tmp/x"}],
                "steps": [{"id": 1, "action": "shell", "expected": "do it"}],
            }

        def revise(self, plan, feedback, actions=None):
            return {
                "reasoning": "fix",
                "acceptance": [{"kind": "file_exists", "path": "/tmp/x"}],
                "steps": [{"id": 1, "action": "shell", "expected": "retry"}],
            }

    class FakeRuntime:
        plugins = FakePluginManager()

    plan = planning.create_plan("goal", runtime=FakeRuntime())
    assert plan.status == "executing"
    assert plan.revision == 0
    assert plan.acceptance[0]["kind"] == "file_exists"

    revised = planning.revise_plan(plan, "acceptance failed", runtime=FakeRuntime())
    assert revised.revision == 1
    assert revised.steps[0].expected == "retry"
    assert plan.status == "replan"


def test_replan_limit(monkeypatch):
    monkeypatch.setattr(planning.config, "AGENT_MAX_REPLANS", 0)
    plan = make_plan()
    assert planning.can_replan(plan) is False
    with pytest.raises(planning.PlanningError):
        planning.revise_plan(plan, "no")
