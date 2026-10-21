from app.session import Session
from app.storage import Storage


def make_session(path):
    return Session(storage_backend=Storage(path), auto_migrate=False)


def test_session_survives_restart(tmp_path):
    path = tmp_path / "agent.db"
    first = make_session(path)
    first.load()
    conv_id = first.conv_id
    assert conv_id

    first.append_history("user", "hello")
    first.add_event("info", "step one", step=1)
    first.set_task("do something")
    first.set_task_state({"plan": [{"step": 1}]})
    first.persist()

    second = make_session(path)
    second.load()

    assert second.conv_id == conv_id
    assert second.history == [{"role": "user", "content": "hello"}]
    assert second.task == "do something"
    assert second.task_state == {"plan": [{"step": 1}]}
    assert second.events[-1]["message"] == "step one"
    assert second.events[-1]["step"] == 1
    assert second.events[-1]["id"] > 0


def test_new_conversation_uses_new_sqlite_session(tmp_path):
    path = tmp_path / "agent.db"
    session = make_session(path)
    session.load()
    old = session.conv_id

    session.append_history("user", "old")
    new = session.new_conversation("new task")

    assert new != old
    assert session.history == []
    assert session.task == "new task"
    assert {row["conv_id"] for row in session.storage.list_sessions()} == {new, old}


def test_clear_events_removes_current_conversation_rows(tmp_path):
    path = tmp_path / "agent.db"
    session = make_session(path)
    session.load()
    session.add_event("info", "one")
    session.add_event("info", "two")
    assert session.storage.totals()["events"] == 2

    session.clear_events()

    assert session.events == []
    assert session.storage.totals()["events"] == 0


def test_wait_events_wakes_on_new_event(tmp_path):
    import threading
    import time

    path = tmp_path / "agent.db"
    session = make_session(path)
    session.load()
    result = []

    def waiter():
        result.extend(session.wait_events(0, timeout=2))

    thread = threading.Thread(target=waiter)
    thread.start()
    time.sleep(0.05)
    session.add_event("info", "wake")
    thread.join(timeout=3)

    assert [event["message"] for event in result] == ["wake"]
