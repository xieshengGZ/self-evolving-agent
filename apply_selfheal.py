#!/usr/bin/env python3
import os, sys

# ---------- 1. config.py 追加配置 ----------
cfg = "app/config.py"
src = open(cfg, encoding="utf-8").read()
extra = '''
# ---- self-heal limits (added) ----
AGENT_MAX_CONSECUTIVE_FAILURES: int = int(_env("AGENT_MAX_CONSECUTIVE_FAILURES", "3"))
AGENT_MAX_RUNTIME: int = int(_env("AGENT_MAX_RUNTIME", "600"))
AGENT_MAX_RESTARTS_PER_WINDOW: int = int(_env("AGENT_MAX_RESTARTS_PER_WINDOW", "5"))
AGENT_RESTART_WINDOW_SEC: int = int(_env("AGENT_RESTART_WINDOW_SEC", "120"))
'''
if "AGENT_MAX_CONSECUTIVE_FAILURES" not in src:
    open(cfg, "a", encoding="utf-8").write(extra)
    print("config.py: 已追加自愈配置")

# ---------- 2. _robust.py 追加重启频率限制 ----------
rb = "app/_robust.py"
src = open(rb, encoding="utf-8").read()
extra = '''

# ---- restart throttle (added) ----
import time as _time

RESTART_HISTORY = os.path.join(config.AGENT_ROOT, "state", "restarts.json")


def check_restart_allowed():
    """返回 (ok, reason)。超过窗口内次数则拒绝。"""
    now = _time.time()
    hist = []
    try:
        with open(RESTART_HISTORY, encoding="utf-8") as f:
            hist = json.load(f)
    except Exception:
        pass
    window = config.AGENT_RESTART_WINDOW_SEC
    hist = [t for t in hist if now - t < window]
    if len(hist) >= config.AGENT_MAX_RESTARTS_PER_WINDOW:
        return False, f"{window}s 内已重启 {len(hist)} 次，超过上限 {config.AGENT_MAX_RESTARTS_PER_WINDOW}"
    hist.append(now)
    try:
        os.makedirs(os.path.dirname(RESTART_HISTORY), exist_ok=True)
        with open(RESTART_HISTORY, "w", encoding="utf-8") as f:
            json.dump(hist, f)
    except Exception:
        pass
    return True, ""
'''
if "check_restart_allowed" not in src:
    open(rb, "a", encoding="utf-8").write(extra)
    print("_robust.py: 已追加 restart 限流")

# ---------- 3. 替换 main.py 的 agent_loop ----------
p = "app/main.py"
src = open(p, encoding="utf-8").read()

NEW_LOOP = '''def agent_loop() -> None:
    """自愈循环：每步 try/except，失败转 error 回传模型，有硬性上限。"""
    import time as _t
    steps = 0
    fails = 0
    started = _t.time()

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
                messages = [{"role": "system", "content": system_prompt}]
                messages.extend(session.get_history())
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
'''

start = src.find("def agent_loop() -> None:")
if start == -1:
    sys.exit("找不到 agent_loop，请把 grep -n 'def agent_loop' app/main.py 的输出发我")

# 找 agent_loop 的结束位置：下一个顶层 def/class/@app/if __name__
rest_lines = src[start:].split("\n")
end_offset = len(rest_lines)
for i, line in enumerate(rest_lines[1:], 1):
    if not line or line[0] in (" ", "\t", "#"):
        continue
    if line.startswith(("def ", "class ", "@", "if __name__")):
        end_offset = i
        break

before = src[:start]
after = "\n".join(rest_lines[end_offset:])
open(p, "w", encoding="utf-8").write(before + NEW_LOOP + "\n" + after)
print("main.py: agent_loop 已替换")

# ---------- 4. system.md 追加自愈章节 ----------
sp = "prompts/system.md"
src = open(sp, encoding="utf-8").read()
section = '''

## 自愈原则（必须遵守）

你会在 history 里收到形如 {"error": "..."} 的 user 消息。这是 agent-services 把你上一步的失败反馈给你，**不是让用户看到就完了**，你必须自己诊断并修复，流程：

1. **诊断**：先用 read_file 读相关文件（源码、日志、配置），或 shell 看 `state/service.log`、`state/crash.log`、`state/session.json`。不要凭空猜测。
2. **定位**：在回复的 reason 里用一句话说明你判断的根因。
3. **修复**：用 write_file 改代码，或用 shell 执行修复命令（装包、改配置、修文件）。改动要最小化，不要顺手重构。
4. **重启**：改完代码后必须发 self_restart。改 prompts/system.md 不需要重启。改 shell 命令不需要重启。
5. **验证**：重启后下一轮先跑一条验证命令（如 import、--version、curl localhost），确认修复生效，再继续原任务。

硬性约束：
- 同一错误出现两次，禁止重复同样的修复动作，必须换思路（读更多文件、换方案、向用户 ask_user）
- 连续失败达到上限会被系统强制停止，所以每次失败都要有实质进展
- 判断是环境问题（缺包、端口占用）优先用 shell 解决，不要改代码
- 判断是逻辑 bug 优先 write_file 改代码 + self_restart
- self_restart 被系统拒绝（重启过频）时，先用 shell 输出当前状态，向用户 ask_user 说明
- 不要试图修改 run.sh 或 .venv，那属于基础设施
- 任何情况下不得输出 DEEPSEEK_API_KEY、AGENT_WEB_TOKEN
'''
if "## 自愈原则" not in src:
    open(sp, "a", encoding="utf-8").write(section)
    print("system.md: 已追加自愈章节")

# ---------- 5. 校验 ----------
import py_compile
for f in ("app/main.py", "app/config.py", "app/_robust.py"):
    py_compile.compile(f, doraise=True)
print("OK 全部语法通过")
