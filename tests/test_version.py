from fastapi.testclient import TestClient

from app.version import CHANNEL, MAJOR, MINOR, PATCH, VERSION


def test_version_is_v1_2_0_stable():
    assert VERSION == "1.2.0"
    assert (MAJOR, MINOR, PATCH) == (1, 2, 0)
    assert CHANNEL == "stable"


def test_version_endpoint_and_state_expose_version():
    from app.main import app

    client = TestClient(app)
    response = client.get("/api/version")
    assert response.status_code == 200
    assert response.json() == {
        "name": "agent-services",
        "version": "1.2.0",
        "channel": "stable",
    }

    state = client.get(
        "/api/state",
        headers={"Authorization": "Bearer test-token"},
    )
    assert state.status_code == 200
    assert state.json()["version"] == "1.2.0"
    assert "last_stop" in state.json()
