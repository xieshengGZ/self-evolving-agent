from fastapi.testclient import TestClient

from app import main, session

AUTH = {"Authorization": "Bearer test-token"}


def test_favicon_is_served():
    client = TestClient(main.app)
    response = client.get("/favicon.svg")
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("image/svg+xml")
    assert b"<svg" in response.content


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
