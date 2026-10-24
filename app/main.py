import os
import threading
from typing import Optional

from fastapi import Depends, FastAPI, Header, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse

from app import classifier, config, context, deepseek, planning, selfupdate, session
from app.kernel import get_runtime
from app.kernel.http import register_plugin_routes
from app.kernel.loop import agent_loop, load_chat_prompt, load_system_prompt, parse_action
from app.kernel.sse import sse_response
from app.version import CHANNEL, VERSION

app = FastAPI(title="agent-services", version=VERSION)

STATIC_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "static")

_runtime = get_runtime()


# ---------------------------------------------------------------------------
# 鉴权
# ---------------------------------------------------------------------------
def _extract_bearer(authorization: Optional[str]) -> Optional[str]:
    if not authorization:
        return None
    parts = authorization.split(None, 1)
    if len(parts) != 2 or parts[0].lower() != "bearer":
        return None
    return parts[1].strip()


def require_token(authorization: Optional[str] = Header(default=None)) -> None:
    token = _extract_bearer(authorization)
    if not token or token != config.AGENT_WEB_TOKEN:
        raise HTTPException(status_code=401, detail="unauthorized")


def require_stream_token(
    authorization: Optional[str] = Header(default=None),
    token: str = "",
) -> None:
    """EventSource 不能设置 Authorization 头，因此额外接受 ?token=。"""
    supplied = _extract_bearer(authorization) or (token or "").strip()
    if not supplied or supplied != config.AGENT_WEB_TOKEN:
        raise HTTPException(status_code=401, detail="unauthorized")


@app.get("/")
def index() -> FileResponse:
    return FileResponse(os.path.join(STATIC_DIR, "index.html"))


FAVICON_CACHE = {"Cache-Control": "public, max-age=86400"}


@app.get("/favicon.svg", include_in_schema=False)
def favicon() -> FileResponse:
    return FileResponse(
        os.path.join(STATIC_DIR, "favicon.svg"),
        media_type="image/svg+xml",
        headers=FAVICON_CACHE,
    )


@app.get("/favicon.ico", include_in_schema=False)
def favicon_ico() -> FileResponse:
    return FileResponse(
        os.path.join(STATIC_DIR, "favicon.ico"),
        media_type="image/x-icon",
        headers=FAVICON_CACHE,
    )


@app.get("/favicon-32.png", include_in_schema=False)
def favicon_png() -> FileResponse:
    return FileResponse(
        os.path.join(STATIC_DIR, "favicon-32.png"),
        media_type="image/png",
        headers=FAVICON_CACHE,
    )


@app.get("/api/version")
def api_version() -> JSONResponse:
    return JSONResponse({"name": "agent-services", "version": VERSION, "channel": CHANNEL})


@app.get("/api/events", dependencies=[Depends(require_stream_token)])
async def api_events(
    request: Request,
    since: int = 0,
    last_event_id: Optional[str] = Header(default=None, alias="Last-Event-ID"),
) -> StreamingResponse:
    value = since
    if last_event_id:
        try:
            value = int(last_event_id)
        except (TypeError, ValueError):
            pass
    return sse_response(request, since=value)


@app.get("/api/state", dependencies=[Depends(require_token)])
def api_state(since: int = 0, since_id: Optional[int] = None) -> JSONResponse:
    if since_id is not None:
        events = [
            event
            for event in session.get_events()
            if int(event.get("id") or 0) > int(since_id)
        ]
    else:
        events = session.get_events(since=since)
    return JSONResponse(
        {
            "version": VERSION,
            "status": session.get_status(),
            "history": session.get_history(),
            "events": events,
            "event_count": session.event_count(),
            "last_event_id": session.last_event_id(),
            "conv_id": session.get_conv_id(),
            "task": session.get_task(),
            "plan": (session.get_task_state() or {}).get("plan"),
            "plan_text": (session.get_task_state() or {}).get("plan_text", ""),
            "last_stop": session.get_last_stop(),
        }
    )


@app.post("/api/clear", dependencies=[Depends(require_token)])
def api_clear() -> JSONResponse:
    """清空当前对话窗口与事件日志（归档旧窗口后开新会话）。"""
    if session.get_status() == "running":
        raise HTTPException(status_code=409, detail="agent is running")
    try:
        planning.clear_plan("clear")
    except Exception:  # noqa: BLE001
        pass
    try:
        session.new_conversation("")
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"清空失败: {e}")
    session.clear_events()
    session.add_event("system", "历史对话已清空")
    session.persist()
    return JSONResponse({"ok": True, "conv_id": session.get_conv_id()})


@app.get("/api/context", dependencies=[Depends(require_token)])
def api_context() -> JSONResponse:
    """展示下一次调用 DeepSeek 时 messages 的拼接明细。"""
    try:
        system_prompt = load_system_prompt()
    except Exception as e:
        system_prompt = "你是 agent-services，只能返回单个 JSON 动作。"
        session.add_event("error", f"读 system.md 失败: {e}")
    try:
        data = context.preview(system_prompt, runtime=_runtime)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"组装上下文失败: {e}")
    return JSONResponse({"ok": True, **data})


def _handle_control(prompt: str) -> JSONResponse:
    value = prompt.strip()
    low = value.lower()
    stop_words = {"停止", "停下", "停", "取消", "中断", "终止", "别做了", "stop", "cancel", "abort"}
    status_words = {"状态", "进度", "进展", "现在什么状态", "当前状态", "status"}
    restart_words = {"重启", "重新启动", "restart", "reload"}

    if value in stop_words or low in stop_words:
        session.request_cancel()
        session.add_event("info", "收到停止请求")
        return JSONResponse({"mode": "control", "action": "stop", "ok": True})

    if value in status_words or low in status_words:
        return JSONResponse(
            {
                "mode": "control",
                "action": "status",
                "ok": True,
                "state": session.snapshot(),
                "plan": planning.snapshot(),
            }
        )

    if value in restart_words or low in restart_words:
        try:
            selfupdate.self_restart(reason="control: " + value)
        except Exception as e:  # noqa: BLE001
            return JSONResponse(
                {"mode": "control", "action": "restart", "ok": False, "error": str(e)},
                status_code=500,
            )
        return JSONResponse({"mode": "control", "action": "restart", "ok": True})

    return JSONResponse({"mode": "control", "action": "noop", "ok": True})


_FALLBACK_ACTION_NAMES = {
    "shell",
    "write_file",
    "read_file",
    "self_restart",
    "read_symbol",
    "read_file_summary",
    "rebuild_index",
    "done",
}

_CONFIRM_CUES = (
    "要继续",
    "继续执行",
    "是否开始",
    "要不要",
    "确认的话",
    "要我现在",
    "需要我继续",
    "需要我执行",
)


def _known_action_names() -> set[str]:
    names = set(_FALLBACK_ACTION_NAMES)
    try:
        names.update(_runtime.plugins.tool_names())
    except Exception:  # noqa: BLE001
        pass
    names.add("done")
    return names


def _looks_like_action_payload(text: object) -> bool:
    if not isinstance(text, str):
        return False
    action = parse_action(text)
    if not isinstance(action, dict):
        return False
    name = action.get("action")
    return isinstance(name, str) and name in _known_action_names()


def _has_pending_work() -> bool:
    """A confirmation is only resumable if a task or non-terminal plan exists."""
    if not (session.get_task() or "").strip():
        return False
    try:
        plan = planning.load_plan()
    except Exception:  # noqa: BLE001
        return True
    if plan is None:
        return True
    return plan.status not in ("done", "failed", "cancelled")


def _last_confirmed_task(prompt: str) -> str:
    """Recover the task when the assistant asked "shall I continue?" and the
    user answers with a short confirmation such as "开始".
    """
    try:
        history = session.get_history()
    except Exception:  # noqa: BLE001
        return ""
    last_assistant = next(
        (item for item in reversed(history) if item.get("role") == "assistant"),
        None,
    )
    if not last_assistant:
        return ""
    content = str(last_assistant.get("content") or "")
    if not content or not any(cue in content for cue in _CONFIRM_CUES):
        return ""

    checked = 0
    for item in reversed(history):
        if item.get("role") != "user":
            continue
        text = str(item.get("content") or "").strip()
        if not text or text == prompt.strip():
            continue
        checked += 1
        if checked > 20:
            break
        if classifier.classify_rules(text) == "task":
            return text
    return ""


def _chat_messages() -> list[dict]:
    """Build chat context without old action-protocol messages.

    Agent history intentionally stores JSON actions for task runs. Feeding those
    to a chat model makes it imitate the protocol and display tool calls as
    prose, so strip only those assistant messages from this view.
    """
    messages = context.build(load_chat_prompt(), runtime=_runtime)["messages"]
    return [
        item
        for item in messages
        if not (
            item.get("role") == "assistant"
            and _looks_like_action_payload(item.get("content"))
        )
    ]


@app.post("/api/chat", dependencies=[Depends(require_token)])
async def api_chat(request: Request) -> JSONResponse:
    try:
        body = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="invalid json")
    prompt = (body or {}).get("prompt", "")
    if not isinstance(prompt, str) or not prompt.strip():
        raise HTTPException(status_code=400, detail="prompt required")

    forced = str((body or {}).get("mode") or "").strip().lower()
    if forced == "auto":
        forced = ""
    if forced not in ("", "chat", "task"):
        raise HTTPException(status_code=400, detail="mode must be auto, chat, or task")

    route = classifier.explain(prompt)
    mode = route["mode"]
    resume_task = ""
    if forced and mode != "control":
        original_mode = mode
        mode = forced
        route = {"mode": mode, "source": "client", "reason": "manual-override"}
        if mode == "task" and original_mode == "continuation":
            candidate = _last_confirmed_task(prompt)
            if candidate:
                resume_task = candidate
    elif mode == "continuation" and not _has_pending_work():
        candidate = _last_confirmed_task(prompt)
        if candidate:
            resume_task = candidate
            mode = "task"
            route = {**route, "mode": "task", "reason": "affirmative-resume"}
        else:
            mode = "chat"
            route = {
                **route,
                "mode": "chat",
                "reason": route.get("reason", "") + "+no-pending-work",
            }
    try:
        session.add_event(
            "route",
            f"mode={mode} source={route.get('source', 'rules')} "
            f"reason={route.get('reason', '')}",
        )
    except Exception:  # noqa: BLE001
        pass
    if mode == "control":
        return _handle_control(prompt)

    if session.get_status() == "running":
        raise HTTPException(status_code=409, detail="agent is running")

    if (body or {}).get("new_conversation") is True:
        try:
            planning.clear_plan("new conversation")
        except Exception:  # noqa: BLE001
            pass
        try:
            session.new_conversation(prompt if mode in ("task", "continuation") else "")
        except Exception:
            session.append_history("user", prompt)
    else:
        session.append_history("user", prompt)

    if mode in ("task", "continuation"):
        try:
            session.set_last_stop("", "")
        except Exception:  # noqa: BLE001
            pass

    if mode == "task":
        old_task = (session.get_task() or "").strip()
        new_task = (resume_task or prompt).strip()
        if old_task and old_task != new_task:
            try:
                planning.clear_plan("new task")
                session.add_event("info", "检测到新任务，已重置旧计划")
            except Exception:  # noqa: BLE001
                pass
        session.set_task(new_task)
        if resume_task:
            session.add_event("info", "从确认对话恢复任务：" + new_task[:120])
    elif mode == "continuation":
        if not (session.get_task() or "").strip():
            session.set_task(prompt)
        session.add_event("info", "继续当前任务")

    try:
        session.record_prompt(prompt)
    except Exception:
        pass
    session.add_event("user", prompt)

    if mode == "chat":
        try:
            session.add_event("info", "对话模式")
            msgs = _chat_messages()
            reply = deepseek.chat_text(msgs)
            if _looks_like_action_payload(reply):
                note = (
                    "这一步需要执行操作，但当前是对话模式。请把要做的事作为任务重新发送；"
                    "例如：先修 1（影响用户体验，改动最小）。"
                )
                session.add_event("warn", "对话模式拦截了动作 JSON，未执行")
                session.append_history("assistant", note)
                session.add_event("assistant", note)
            else:
                session.append_history("assistant", reply)
                session.add_event("assistant", str(reply))
        except Exception as e:
            session.add_event("error", f"chat 失败: {e}")
            session.append_history("assistant", f"[error] {e}")
        session.persist()
        return JSONResponse({"mode": "chat", "ok": True})

    session.set_status("running")
    session.clear_cancel()
    session.persist()

    thread = threading.Thread(target=agent_loop, daemon=True)
    thread.start()
    return JSONResponse({"mode": mode, "ok": True, "status": "running"})


@app.post("/api/stop", dependencies=[Depends(require_token)])
def api_stop() -> JSONResponse:
    session.request_cancel()
    session.add_event("info", "收到停止请求")
    return JSONResponse({"ok": True})


@app.post("/api/restart", dependencies=[Depends(require_token)])
def api_restart() -> JSONResponse:
    try:
        selfupdate.self_restart(reason="manual restart via API")
    except Exception as e:  # noqa: BLE001
        return JSONResponse({"ok": False, "error": str(e)}, status_code=500)
    return JSONResponse({"ok": True})


@app.get("/api/usage", dependencies=[Depends(require_token)])
def api_usage(conv_id: str = "", limit: int = 50) -> JSONResponse:
    """Token 消耗统计：全局 + 当前会话 + 最近调用记录。"""
    from app import usage as _usage

    cid = conv_id or session.get_conv_id()
    try:
        data = _usage.summary(conv_id=cid, limit=max(1, min(limit, 500)))
    except Exception as e:  # noqa: BLE001
        raise HTTPException(status_code=500, detail=f"统计失败: {e}")
    return JSONResponse(data)


@app.post("/api/usage/reset", dependencies=[Depends(require_token)])
def api_usage_reset() -> JSONResponse:
    from app import usage as _usage

    return JSONResponse(_usage.reset())


@app.post("/api/rollback", dependencies=[Depends(require_token)])
def api_rollback() -> JSONResponse:
    try:
        selfupdate.rollback_latest()
    except Exception as e:  # noqa: BLE001
        return JSONResponse({"ok": False, "error": str(e)}, status_code=500)
    return JSONResponse({"ok": True})


# ---------------------------------------------------------------------------
# 启动
# ---------------------------------------------------------------------------
def _startup() -> None:
    config.ensure_dirs()
    session.load()
    if session.get_status() == "running":
        session.set_status("idle")
        session.add_event("info", "上次运行被中断")
        session.persist()


_startup()
_runtime.start()

# manual ops
from app._robust import register as _register_manual

_register_manual(app)

from app.memory_api import register as _register_memory

_register_memory(app, require_token)

register_plugin_routes(app, _runtime, require_token)


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(
        "app.main:app",
        host="0.0.0.0",
        port=config.AGENT_PORT,
        log_level="info",
    )
