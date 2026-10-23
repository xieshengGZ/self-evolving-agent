"""Server-Sent Events bridge for the live session.

The stream is intentionally read-only: clients can watch events/status but all
mutations still go through the normal POST endpoints.
"""
from __future__ import annotations

import asyncio
import json
from typing import Any, AsyncIterator, Dict, Optional

from fastapi import Request
from fastapi.responses import StreamingResponse

from app import session


def _format(event: str, data: Any, event_id: Optional[int] = None) -> str:
    lines = []
    if event_id is not None:
        lines.append(f"id: {event_id}")
    lines.append(f"event: {event}")
    payload = json.dumps(data, ensure_ascii=False, default=str)
    for line in payload.splitlines() or [""]:
        lines.append(f"data: {line}")
    return "\n".join(lines) + "\n\n"


def state_payload() -> Dict[str, Any]:
    return {
        "status": session.get_status(),
        "history": session.get_history(),
        "event_count": session.event_count(),
        "last_event_id": session.last_event_id(),
        "conv_id": session.get_conv_id(),
        "task": session.get_task(),
        "last_stop": session.get_last_stop(),
    }


def _backlog(last_id: int) -> list[Dict[str, Any]]:
    return [
        event
        for event in session.get_events()
        if int(event.get("id") or 0) > int(last_id)
    ]


async def event_stream(
    request: Request,
    since: int = 0,
    keepalive: float = 15.0,
) -> AsyncIterator[str]:
    last_id = max(0, int(since or 0))
    loop = asyncio.get_running_loop()
    queue = session.subscribe_events(loop)

    try:
        yield "retry: 3000\n\n"

        for event in _backlog(last_id):
            last_id = max(last_id, int(event.get("id") or 0))
            yield _format("log", event, last_id)
        yield _format("state", state_payload())

        while True:
            try:
                event = await asyncio.wait_for(queue.get(), timeout=keepalive)
            except asyncio.TimeoutError:
                if await request.is_disconnected():
                    break
                yield ": keepalive\n\n"
                continue

            if await request.is_disconnected():
                break

            batch = [event]
            while True:
                try:
                    batch.append(queue.get_nowait())
                except asyncio.QueueEmpty:
                    break

            sent = False
            for item in batch:
                event_id = int(item.get("id") or 0)
                if event_id and event_id <= last_id:
                    continue
                last_id = max(last_id, event_id)
                yield _format("log", item, event_id or None)
                sent = True
            if sent:
                yield _format("state", state_payload())
    finally:
        session.unsubscribe_events(queue)


def sse_response(
    request: Request,
    since: int = 0,
    keepalive: float = 15.0,
) -> StreamingResponse:
    return StreamingResponse(
        event_stream(request, since=since, keepalive=keepalive),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )
