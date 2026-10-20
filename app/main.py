import json
import os
import re
import threading
import time
from typing import Any, Dict, List, Optional

from fastapi import Depends, FastAPI, Header, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from app import actions, classifier, config, deepseek, selfupdate, session

app = FastAPI(title="agent-services")

STATIC_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "static")


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


# ---------------------------------------------------------------------------
# 提示词加载（热加载）
# ---------------------------------------------------------------------------
FALLBACK_PROMPT = (
    "你是一个可以操作 Linux 系统的 AI Agent。每次只返回一个 JSON 动作，"
    "不要输出多余文字。可用动作：shell、write_file、read_file、self_restart、done。"
    "完成后返回 {\"action\":\"done\",\"message\":\"...\"}。"
    "不得输出任何密钥或令牌。小步执行并验证结果。"
)


def load_system_prompt() -> str:
    path = os.path.join(config.AGENT_ROOT, "prompts", "system.md")
    try:
        with open(path, "r", encoding="utf-8") as f:
            content = f.read().strip()
        if content:
            return content
    except FileNotFoundError:
        pass
    except Exception:
        pass
    return FALLBACK_PROMPT


# ---------------------------------------------------------------------------
# JSON 动作解析
# ---------------------------------------------------------------------------
def parse_action(text: Any) -> Optional[Dict[str, Any]]:
    """兼容 str 和 dict：DeepSeek 客户端若返回已解析的 dict，直接使用。"""
    if isinstance(text, dict):
        return text
    if not isinstance(text, str):
        return None
    try:
        text = text.strip()
    except Exception:
        return None
    if not text:
        return None
    # 剥离 markdown 围栏
    if text.startswith("```"):
        first_nl = text.find("\n")
        if first_nl != -1:
            text = text[first_nl + 1:]
            if text.rstrip().endswith("```"):
                text = text.rstrip()[:-3]
    # 去掉尾部杂标签（</parameter> 等）
    text = re.sub(r"\s*</(?:parameter|response|output|content|answer)>\s*$", "", text,
                  flags=re.I)
    text = text.strip()
    if not text:
        return None
    try:
        obj = json.loads(text)
    except Exception:
        return None
    if not isinstance(obj, dict):
        return None
    return obj

def execute_action(action: Dict[str, Any]) -> Dict[str, Any]:
    name = action.get("action")
    try:
        if name == "shell":
            return actions.do_shell(
                command=str(action.get("command", "")),
                timeout=int(action.get("timeout", config.AGENT_CMD_TIMEOUT)),
            )
        if name == "write_file":
            return actions.do_write_file(
                path=str(action.get("path", "")),
                content=str(action.get("content", "")),
            )
        if name == "read_file":
            return actions.do_read_file(
                path=str(action.get("path", "")),
                max_bytes=int(action.get("max_bytes", 20000)),
            )
        if name == "self_restart":
            return actions.do_self_restart(reason=str(action.get("reason", "")))
        if name == "read_symbol":
            return actions.do_read_symbol(symbol=str(action.get("symbol", "")))
        if name == "read_file_summary":
            return actions.do_read_file_summary(path=str(action.get("path", "")))
        if name == "rebuild_index":
            return actions.do_rebuild_index()
        if name == "done":
            return {"result_of": "done", "message": str(action.get("message", ""))}
        return {"error": f"unknown action: {name}"}
    except Exception as e:  # noqa: BLE001
        return {"error": f"{type(e).__name__}: {e}"}


# ---------------------------------------------------------------------------
# Agent loop
# ---------------------------------------------------------------------------
def _append_event(kind: str, message: str) -> None:
    session.add_event(kind, message)


def agent_loop() -> None:
    """自愈循环：每步 try/except，失败转 error 回传模型，有硬性上限。"""
    import time as _t
    steps = 0
    fails = 0
    started = _t.time()
    last_action_str = ""
    repeat_count = 0

    def _fail(msg: str) -> None:
        try:
            session.append_history("user", json.dumps({"error": msg}, ensure_ascii=False))
        except Exception:
            pass
        _append_event("error", msg)
        try:
            session.persist()
        except Exception:
            pass

    try:
        while steps < config.AGENT_MAX_STEPS:
            if session.is_cancelled():
                _append_event("info", "任务被用户取消")
                break
            if _t.time() - started > config.AGENT_MAX_RUNTIME:
                _append_event("error", f"超过最大运行时长 {config.AGENT_MAX_RUNTIME}s")
                break
            if fails >= config.AGENT_MAX_CONSECUTIVE_FAILURES:
                _append_event("error", f"连续失败 {fails} 次，停止自愈")
                break
            steps += 1
            _append_event("info", f"--- step {steps}/{config.AGENT_MAX_STEPS} ---")

            try:
                system_prompt = load_system_prompt()
            except Exception as e:
                system_prompt = "你是 agent-services，只能返回单个 JSON 动作。"
                _append_event("error", f"读 system.md 失败: {e}")

            try:
                messages = session.build_messages(system_prompt)
            except Exception as e:
                _append_event("error", f"组装 messages 失败: {e}")
                break

            try:
                reply = deepseek.chat(messages)
            except Exception as e:
                fails += 1
                _fail(f"DeepSeek 调用失败({fails}/{config.AGENT_MAX_CONSECUTIVE_FAILURES}): {type(e).__name__}: {e}")
                continue

            try:
                session.append_history("assistant", reply)
                _append_event("assistant", reply)
                session.persist()
            except Exception as e:
                _append_event("error", f"写 history 失败: {e}")

            try:
                action = parse_action(reply)
            except Exception as e:
                fails += 1
                _fail(f"parse_action 异常: {type(e).__name__}: {e}")
                continue
            if action is None:
                fails += 1
                _fail("无法解析为 JSON 动作，请只返回一个 JSON 对象，不要加解释或围栏")
                continue
            fails = 0

            _act_str = json.dumps(action, ensure_ascii=False, sort_keys=True)
            if _act_str == last_action_str:
                repeat_count += 1
            else:
                last_action_str = _act_str
                repeat_count = 0
            if repeat_count >= 3:
                _append_event("error", "连续 3 次相同动作，强制终止")
                break

            name = action.get("action")
            _append_event("action", json.dumps(action, ensure_ascii=False)[:2000])

            if name == "done":
                _append_event("done", str(action.get("message", "")))
                session.persist()
                break

            if name == "self_restart":
                try:
                    from app._robust import check_restart_allowed
                    ok, reason = check_restart_allowed()
                except Exception:
                    ok, reason = True, ""
                if not ok:
                    _fail(f"self_restart 被拒绝: {reason}")
                    continue

            try:
                result = execute_action(action)
                fails = 0
            except Exception as e:
                fails += 1
                result = {"error": f"{type(e).__name__}: {e}"}
                _append_event("error", f"execute_action 失败({fails}/{config.AGENT_MAX_CONSECUTIVE_FAILURES}): {result['error']}")

            try:
                session.append_history("user", json.dumps(result, ensure_ascii=False))
                try:
                    session.record_step(action, result)
                except Exception:
                    pass
                _append_event("result", json.dumps(result, ensure_ascii=False)[:2000])
                session.persist()
            except Exception as e:
                _append_event("error", f"写结果失败: {e}")

            if session.is_cancelled():
                _append_event("info", "任务被用户取消")
                break

        if steps >= config.AGENT_MAX_STEPS:
            _append_event("info", f"达到最大步数 {config.AGENT_MAX_STEPS}")
    except Exception as e:
        import traceback
        _append_event("error", f"agent_loop 致命异常: {type(e).__name__}: {e}")
        try:
            session.append_history("user", json.dumps(
                {"error": f"agent_loop 致命异常: {e}", "trace": traceback.format_exc()[-2000:]},
                ensure_ascii=False))
        except Exception:
            pass
    finally:
        try:
            session.set_status("idle")
            session.clear_cancel()
            session.persist()
        except Exception:
            pass

@app.get("/")
def index() -> FileResponse:
    return FileResponse(os.path.join(STATIC_DIR, "index.html"))


@app.get("/api/state", dependencies=[Depends(require_token)])
def api_state(since: int = 0) -> JSONResponse:
    events = session.get_events(since=since)
    return JSONResponse(
        {
            "status": session.get_status(),
            "history": session.get_history(),
            "events": events,
            "event_count": session.event_count(),
        }
    )


@app.post("/api/clear", dependencies=[Depends(require_token)])
def api_clear() -> JSONResponse:
    """清空当前对话窗口与事件日志（归档旧窗口后开新会话）。"""
    if session.get_status() == "running":
        raise HTTPException(status_code=409, detail="agent is running")
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
        _append_event("error", f"读 system.md 失败: {e}")
    try:
        data = session.build_context_preview(system_prompt)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"组装上下文失败: {e}")
    return JSONResponse({"ok": True, **data})


@app.post("/api/chat", dependencies=[Depends(require_token)])
async def api_chat(request: Request) -> JSONResponse:
    try:
        body = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="invalid json")
    prompt = (body or {}).get("prompt", "")
    if not isinstance(prompt, str) or not prompt.strip():
        raise HTTPException(status_code=400, detail="prompt required")

    if session.get_status() == "running":
        raise HTTPException(status_code=409, detail="agent is running")

    if (body or {}).get("new_conversation") is True:
        try:
            session.new_conversation(prompt)
        except Exception:
            session.append_history("user", prompt)
    else:
        session.append_history("user", prompt)
    try:
        session.record_prompt(prompt)
    except Exception:
        pass
    session.add_event("user", prompt)

    mode = classifier.classify(prompt)
    if mode == "chat":
        try:
            session.add_event("info", "闲聊模式")
            msgs = session.build_messages(load_system_prompt())
            reply = deepseek.chat(msgs)
            if isinstance(reply, dict):
                reply = reply.get("message") or reply.get("content") or str(reply)
            session.append_history("assistant", str(reply))
            session.add_event("assistant", str(reply))
        except Exception as e:
            session.add_event("error", f"chat 失败: {e}")
            session.append_history("assistant", f"[error] {e}")
        session.set_status("idle")
        session.persist()
        return JSONResponse({"mode": "chat", "ok": True})

    session.set_status("running")
    session.clear_cancel()
    session.persist()

    t = threading.Thread(target=agent_loop, daemon=True)
    t.start()
    return JSONResponse({"ok": True, "status": "running"})


@app.post("/api/stop", dependencies=[Depends(require_token)])
def api_stop() -> JSONResponse:
    session.request_cancel()
    actions.kill_current_process()
    session.add_event("info", "收到停止请求")
    return JSONResponse({"ok": True})


@app.post("/api/restart", dependencies=[Depends(require_token)])
def api_restart() -> JSONResponse:
    try:
        selfupdate.self_restart(reason="manual restart via API")
    except Exception as e:  # noqa: BLE001
        return JSONResponse({"ok": False, "error": str(e)}, status_code=500)
    return JSONResponse({"ok": True})


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

# manual ops
from app._robust import register as _register_manual
_register_manual(app)

from app.memory_api import register as _register_memory
_register_memory(app, require_token)


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(
        "app.main:app",
        host="0.0.0.0",
        port=config.AGENT_PORT,
        log_level="info",
    )