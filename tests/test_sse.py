import asyncio
import json

from fastapi import FastAPI, Request
from fastapi.testclient import TestClient

from app import session
from app.kernel.sse import sse_response


def test_event_stream_sends_backlog_and_initial_state():
    session.add_event("test", "sse-marker")
    app = FastAPI()

    @app.get("/events")
    def events(request: Request, since: int = 0):
        return sse_response(request, since=since, keepalive=0.05)

    client = TestClient(app)
    with client.stream("GET", "/events?since=0") as response:
        assert response.status_code == 200
        assert "text/event-stream" in response.headers["content-type"]

        lines = response.iter_lines()
        saw_marker = False
        state = None
        for line in lines:
            if line.startswith("data: ") and "sse-marker" in line:
                saw_marker = True
            if line == "event: state":
                state = json.loads(next(lines)[len("data: "):])
                break

    assert saw_marker is True
    assert state is not None
    assert "status" in state
    assert "history" in state
    assert "last_event_id" in state


def test_subscribers_receive_events_pushed_from_worker_thread():
    async def scenario():
        loop = asyncio.get_running_loop()
        queue = session.subscribe_events(loop)
        try:
            await asyncio.to_thread(session.add_event, "info", "pushed")
            return await asyncio.wait_for(queue.get(), timeout=2)
        finally:
            session.unsubscribe_events(queue)

    event = asyncio.run(scenario())
    assert event["message"] == "pushed"
    assert event["id"] > 0


def test_main_sse_requires_token_and_streams_state():
    from app.main import app

    client = TestClient(app)
    assert client.get("/api/events?token=wrong").status_code == 401

    with client.stream("GET", "/api/events?token=test-token&since=0") as response:
        assert response.status_code == 200
        for line in response.iter_lines():
            if line == "event: state":
                break
