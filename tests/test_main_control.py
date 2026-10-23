from fastapi.testclient import TestClient

from app import main, session

AUTH = {"Authorization": "Bearer test-token"}


def test_favicon_is_served():
    client = TestClient(main.app)
    response = client.get("/favicon.svg")
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("image/svg+xml")
    assert b"<svg" in response.content
    assert response.headers["cache-control"].startswith("public")


def test_ico_and_png_favicons_are_served():
    client = TestClient(main.app)
    ico = client.get("/favicon.ico")
    assert ico.status_code == 200
    assert ico.headers["content-type"].startswith("image/x-icon")
    assert ico.content.startswith(b"\x00\x00\x01\x00")
    png = client.get("/favicon-32.png")
    assert png.status_code == 200
    assert png.headers["content-type"].startswith("image/png")
    assert png.content.startswith(b"\x89PNG")


def test_control_works_while_agent_is_running():
    client = TestClient(main.app)
    session.set_status("running")
    try:
        status = client.post("/api/chat", json={"prompt": "状态"}, headers=AUTH)
        assert status.status_code == 200
        assert status.json()["action"] == "status"

        stop = client.post("/api/chat", json={"prompt": "停止"}, headers=AUTH)
        assert stop.status_code == 200
        assert stop.json()["action"] == "stop"
        assert session.is_cancelled() is True
    finally:
        session.clear_cancel()
        session.set_status("idle")


def test_chat_mode_uses_conversation_prompt(monkeypatch):
    client = TestClient(main.app)
    captured = {}
    session.set_status("idle")

    def fake_build(prompt, runtime=None):
        captured["prompt"] = prompt
        return {"messages": [{"role": "system", "content": prompt}]}

    monkeypatch.setattr(main, "load_chat_prompt", lambda: "CHAT_PROMPT_ONLY")
    monkeypatch.setattr(main.context, "build", fake_build)
    monkeypatch.setattr(main.deepseek, "chat_text", lambda messages: "纯文本回答")

    response = client.post("/api/chat", json={"prompt": "你好"}, headers=AUTH)
    assert response.status_code == 200
    assert response.json()["mode"] == "chat"
    assert captured["prompt"] == "CHAT_PROMPT_ONLY"


def test_last_confirmed_task_recovers_previous_task(monkeypatch):
    history = [
        {"role": "user", "content": "先修 1，改动前先提交 github。"},
        {"role": "assistant", "content": "要我现在继续执行这个任务吗？确认的话我就开始。"},
    ]
    monkeypatch.setattr(main.session, "get_history", lambda: history)
    assert main._last_confirmed_task("开始") == "先修 1，改动前先提交 github。"


def test_chat_messages_drop_action_history(monkeypatch):
    messages = [
        {"role": "system", "content": "chat only"},
        {"role": "assistant", "content": '{"action":"shell","command":"ls"}'},
        {"role": "assistant", "content": "普通回答"},
        {"role": "user", "content": "你好"},
    ]
    monkeypatch.setattr(
        main.context, "build", lambda prompt, runtime=None: {"messages": messages}
    )
    filtered = main._chat_messages()
    assert all(
        not main._looks_like_action_payload(item.get("content"))
        for item in filtered
        if item.get("role") == "assistant"
    )


def test_chat_blocks_action_json_reply(monkeypatch):
    client = TestClient(main.app)
    session.new_conversation("")
    session.set_status("idle")
    monkeypatch.setattr(main, "load_chat_prompt", lambda: "CHAT_PROMPT_ONLY")
    monkeypatch.setattr(
        main.context,
        "build",
        lambda prompt, runtime=None: {"messages": [{"role": "system", "content": prompt}]},
    )
    monkeypatch.setattr(
        main.deepseek,
        "chat_text",
        lambda messages: '{"action":"shell","command":"ls"}',
    )

    response = client.post("/api/chat", json={"prompt": "你好"}, headers=AUTH)
    assert response.status_code == 200
    assert response.json()["mode"] == "chat"
    assert session.get_history()[-1]["role"] == "assistant"
    assert "action" not in session.get_history()[-1]["content"]
    assert "任务重新发送" in session.get_history()[-1]["content"]


def test_start_resumes_confirmed_task(monkeypatch):
    client = TestClient(main.app)
    session.new_conversation("")
    session.set_task("旧任务")
    session.set_status("idle")
    monkeypatch.setattr(main, "_has_pending_work", lambda: False)
    monkeypatch.setattr(main, "_last_confirmed_task", lambda prompt: "先修 1，改动前先提交 github。")
    started = {}
    monkeypatch.setattr(main, "agent_loop", lambda: started.setdefault("called", True))

    response = client.post("/api/chat", json={"prompt": "开始"}, headers=AUTH)
    assert response.status_code == 200
    assert response.json()["mode"] == "task"
    assert session.get_task() == "先修 1，改动前先提交 github。"
    session.set_status("idle")
