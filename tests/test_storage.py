import json

from app.storage import Storage


def test_session_messages_events_roundtrip(tmp_path):
    db = Storage(tmp_path / "agent.db")
    db.init()
    db.create_session("conv-1", title="demo")
    db.append_message("conv-1", "user", "hello")
    db.append_message("conv-1", "assistant", "hi")
    first = db.append_event("conv-1", "info", "one", extra={"x": 1})
    second = db.append_event("conv-1", "info", "two")
    db.save_session(
        "conv-1",
        status="idle",
        task="goal",
        task_state={"plan": [{"step": 1}]},
        title="demo",
    )

    data = db.load_current()
    assert data["conv_id"] == "conv-1"
    assert data["task"] == "goal"
    assert data["task_state"] == {"plan": [{"step": 1}]}
    assert data["history"] == [
        {"role": "user", "content": "hello"},
        {"role": "assistant", "content": "hi"},
    ]
    assert [event["message"] for event in data["events"]] == ["one", "two"]
    assert data["events"][0]["x"] == 1
    assert first < second

    assert db.events_after("conv-1", first) == [
        {"id": second, "ts": data["events"][1]["ts"], "kind": "info", "message": "two"}
    ]
    assert db.events_after("conv-1", second) == []
    assert db.list_sessions()[0]["title"] == "demo"
    assert db.totals() == {"sessions": 1, "messages": 2, "events": 2, "plans": 0}


def test_plans_roundtrip(tmp_path):
    db = Storage(tmp_path / "agent.db")
    db.init()
    db.create_session("conv-1")

    plan_id = db.save_plan("conv-1", "goal", [{"id": 1, "status": "todo"}])
    plans = db.list_plans("conv-1")

    assert plan_id > 0
    assert len(plans) == 1
    assert plans[0]["goal"] == "goal"
    assert plans[0]["steps"] == [{"id": 1, "status": "todo"}]
    assert plans[0]["status"] == "active"


def test_migrate_session_json_once(tmp_path):
    source = tmp_path / "session.json"
    source.write_text(
        json.dumps(
            {
                "conv_id": "legacy-1",
                "status": "running",
                "task": "old task",
                "task_state": {"plan": [{"text": "step"}]},
                "history": [
                    {"role": "user", "content": "hello"},
                    {"role": "assistant", "content": '{"action":"done"}'},
                ],
                "events": [
                    {"ts": 1.0, "kind": "user", "message": "hello", "extra": "kept"},
                    {"ts": 2.0, "kind": "done", "message": "ok"},
                ],
            }
        ),
        encoding="utf-8",
    )

    db = Storage(tmp_path / "agent.db")
    result = db.migrate_json(source)

    assert result["migrated"] is True
    assert result["messages"] == 2
    assert result["events"] == 2
    assert result["plans"] == 1

    data = db.load_current()
    assert data["conv_id"] == "legacy-1"
    assert data["status"] == "idle"
    assert data["task"] == "old task"
    assert [item["content"] for item in data["history"]] == [
        "hello",
        '{"action":"done"}',
    ]
    assert data["events"][0]["extra"] == "kept"
    assert db.list_plans("legacy-1")[0]["steps"] == [{"text": "step"}]

    again = db.migrate_json(source)
    assert again["migrated"] is False
    assert db.totals()["messages"] == 2


def test_migrate_skips_when_database_has_data(tmp_path):
    source = tmp_path / "session.json"
    source.write_text(json.dumps({"history": [{"role": "user", "content": "x"}]}))

    db = Storage(tmp_path / "agent.db")
    db.init()
    db.create_session("already-here")

    result = db.migrate_json(source)
    assert result["migrated"] is False
    assert "already has sessions" in result["reason"]
    assert db.totals()["messages"] == 0


def test_plan_acceptance_and_update(tmp_path):
    db = Storage(tmp_path / "agent.db")
    db.init()
    db.create_session("conv-plan")

    plan_id = db.save_plan(
        "conv-plan",
        "ship it",
        [{"id": 1, "status": "pending"}],
        "executing",
        acceptance=[{"kind": "file_exists", "path": "/tmp/x"}],
        current_step=0,
        revision=1,
    )
    plan = db.get_plan(plan_id)
    assert plan["acceptance"] == [{"kind": "file_exists", "path": "/tmp/x"}]
    assert plan["revision"] == 1

    db.update_plan(
        plan_id,
        status="done",
        steps=[{"id": 1, "status": "done"}],
        current_step=1,
    )
    updated = db.get_plan(plan_id)
    assert updated["status"] == "done"
    assert updated["steps"] == [{"id": 1, "status": "done"}]
    assert updated["current_step"] == 1
    assert db.list_plans("conv-plan")[0]["status"] == "done"
