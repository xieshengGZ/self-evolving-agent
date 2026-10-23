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
