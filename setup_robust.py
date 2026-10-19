#!/usr/bin/env python3
import py_compile, sys

ROBUST = '''"""手动运维扩展：崩溃持久化、手动执行、手动重启、/manual 页面。"""
import json, os, subprocess, sys, threading, traceback
from datetime import datetime
from typing import Optional
from fastapi import Header, HTTPException, Request
from fastapi.responses import HTMLResponse
from app import config, session

CRASH_LOG = os.path.join(config.AGENT_ROOT, "state", "crash.log")


def _write_crash(text):
    try:
        os.makedirs(os.path.dirname(CRASH_LOG), exist_ok=True)
        with open(CRASH_LOG, "a", encoding="utf-8") as f:
            f.write("\\n=== " + datetime.now().isoformat() + " ===\\n" + text + "\\n")
    except Exception:
        pass
    try:
        session.add_event("crash", text[:4000])
        session.persist()
    except Exception:
        pass


def _install_hooks():
    def hook(et, ev, tb):
        _write_crash("".join(traceback.format_exception(et, ev, tb)))
        sys.__excepthook__(et, ev, tb)
    sys.excepthook = hook
    try:
        threading.excepthook = lambda a: hook(a.exc_type, a.exc_value, a.exc_traceback)
    except Exception:
        pass


def _load_prev_crash():
    if os.path.exists(CRASH_LOG):
        try:
            with open(CRASH_LOG, encoding="utf-8") as f:
                text = f.read()[-4000:]
            if text.strip():
                session.add_event("crash_prev", "上次崩溃:\\n" + text)
        except Exception:
            pass


def _check_auth(authorization):
    if not authorization:
        raise HTTPException(401, "missing auth")
    parts = authorization.split(None, 1)
    if len(parts) != 2 or parts[0].lower() != "bearer":
        raise HTTPException(401, "bad auth")
    if parts[1].strip() != config.AGENT_WEB_TOKEN:
        raise HTTPException(401, "bad token")


def _run_shell(cmd, timeout=120):
    try:
        p = subprocess.run(cmd, shell=True, capture_output=True, text=True,
                           timeout=timeout, start_new_session=True)
        return {"exit_code": p.returncode,
                "stdout": p.stdout[-8000:], "stderr": p.stderr[-8000:]}
    except subprocess.TimeoutExpired:
        return {"error": "timeout " + str(timeout) + "s"}
    except Exception as e:
        return {"error": type(e).__name__ + ": " + str(e)}


MANUAL_HTML = """<!doctype html><html><head><meta charset="utf-8">
<title>manual ops</title><style>
body{font-family:monospace;background:#111;color:#ddd;padding:1em;}
textarea,input{width:100%;background:#222;color:#0f0;border:1px solid #444;padding:6px;font-family:monospace;box-sizing:border-box;}
button{background:#333;color:#0ff;border:1px solid #555;padding:6px 14px;cursor:pointer;margin-right:6px;}
pre{background:#000;padding:1em;overflow:auto;max-height:500px;white-space:pre-wrap;}
.row{margin:8px 0;}
</style></head><body>
<h3>manual ops - 手动救援</h3>
<div class="row">TOKEN: <input id="tk"></div>
<div class="row">命令: <textarea id="cmd" rows="4">ls -la</textarea></div>
<div class="row">
  <button onclick="ex()">执行</button>
  <button onclick="rs()">重启服务</button>
  <button onclick="st()">状态</button>
  <button onclick="lg()">崩溃日志</button>
</div>
<pre id="out">ready</pre>
<script>
const tk=document.getElementById('tk'),out=document.getElementById('out');
tk.value=localStorage.getItem('manual_token')||'';
tk.onchange=()=>localStorage.setItem('manual_token',tk.value);
function show(x){out.textContent=typeof x==='string'?x:JSON.stringify(x,null,2);}
async function ex(){
 const r=await fetch('/api/exec',{method:'POST',
  headers:{'Content-Type':'application/json','Authorization':'Bearer '+tk.value},
  body:JSON.stringify({command:document.getElementById('cmd').value})});
 show(await r.json());}
async function rs(){
 if(!confirm('确认重启?'))return;
 const r=await fetch('/api/restart',{method:'POST',headers:{'Authorization':'Bearer '+tk.value}});
 show(await r.json());}
async function st(){
 const r=await fetch('/api/state',{headers:{'Authorization':'Bearer '+tk.value}});
 show(await r.json());}
async function lg(){
 const r=await fetch('/api/exec',{method:'POST',
  headers:{'Content-Type':'application/json','Authorization':'Bearer '+tk.value},
  body:JSON.stringify({command:'tail -n 80 state/crash.log 2>/dev/null; echo ---; tail -n 40 state/last_crash.log 2>/dev/null'})});
 show(await r.json());}
</script></body></html>"""


def register(app):
    _install_hooks()

    @app.post("/api/exec")
    async def api_exec(req: Request, authorization: Optional[str] = Header(None)):
        _check_auth(authorization)
        body = await req.json()
        cmd = (body.get("command") or "").strip()
        if not cmd:
            raise HTTPException(400, "empty command")
        session.add_event("manual_exec", cmd)
        result = _run_shell(cmd)
        session.add_event("manual_result", json.dumps(result, ensure_ascii=False)[:2000])
        session.persist()
        return result

    @app.post("/api/restart")
    async def api_restart(authorization: Optional[str] = Header(None)):
        _check_auth(authorization)
        session.add_event("info", "手动重启")
        session.persist()
        def _do():
            import time as _t
            _t.sleep(0.3)
            os._exit(1)
        threading.Thread(target=_do, daemon=True).start()
        return {"status": "restarting"}

    @app.get("/manual", response_class=HTMLResponse)
    async def manual_page():
        return MANUAL_HTML

    _load_prev_crash()
'''

open("app/_robust.py", "w", encoding="utf-8").write(ROBUST)

src = open("app/main.py", encoding="utf-8").read()
if "_register_manual" not in src:
    lines = src.split("\n")
    idx = None
    for i, line in enumerate(lines):
        if line.strip() == "_startup()" and not line.startswith(" "):
            idx = i
            break
    if idx is None:
        sys.exit("找不到模块级 _startup() 调用，请把 tail -20 app/main.py 发我")
    insert = [
        "",
        "# manual ops",
        "from app._robust import register as _register_manual",
        "_register_manual(app)",
    ]
    lines = lines[:idx + 1] + insert + lines[idx + 1:]
    open("app/main.py", "w", encoding="utf-8").write("\n".join(lines))
    print("main.py: 已注册 _robust")
else:
    print("main.py: 已注册，跳过")

py_compile.compile("app/_robust.py", doraise=True)
py_compile.compile("app/main.py", doraise=True)
print("OK _robust.py 已就绪")
