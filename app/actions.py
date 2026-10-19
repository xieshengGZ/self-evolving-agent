import json
import os
import re
import shutil
import signal
import subprocess
import time
from typing import Any, Dict, Optional

from app import config

# ---------------------------------------------------------------------------
# 危险命令拦截
# ---------------------------------------------------------------------------

# 明显破坏性命令的正则列表（大小写不敏感）
_DANGEROUS_PATTERNS = [
    r"rm\s+(-[a-zA-Z]*\s+)*-?[a-zA-Z]*r[a-zA-Z]*f[a-zA-Z]*\s+/\s*$",
    r"rm\s+(-[a-zA-Z]*\s+)*-?[a-zA-Z]*f[a-zA-Z]*r[a-zA-Z]*\s+/\s*$",
    r"rm\s+-rf\s+/\s",
    r"rm\s+-rf\s+/\*",
    r"rm\s+-fr\s+/\s",
    r"rm\s+-fr\s+/\*",
    r"\bmkfs(\.[a-z0-9]+)?\b",
    r"\bdd\s+.*\bif=",
    r"\bshutdown\b",
    r"\breboot\b",
    r"\bpoweroff\b",
    r"\bhalt\b",
    r">\s*/etc/ssh/",
    r">>\s*/etc/ssh/",
    r"\btee\s+.*/etc/ssh/",
    r"\bchmod\s+.*\s+/etc/ssh/",
    r"\bchown\s+.*\s+/etc/ssh/",
    r":\(\)\s*\{\s*:\|:&\s*\};:",  # fork bomb
    r"\bmv\s+.*\s+/dev/null",
    r">\s*/dev/sd[a-z]",
    r"\bwipefs\b",
    r"\bshred\b.*/dev/",
]

_DANGEROUS_RE = [re.compile(p, re.IGNORECASE) for p in _DANGEROUS_PATTERNS]


def is_dangerous_command(command: str) -> Optional[str]:
    """如果命令明显破坏性，返回命中的模式描述；否则返回 None。"""
    if not command:
        return None
    for pattern, regex in zip(_DANGEROUS_PATTERNS, _DANGEROUS_RE):
        if regex.search(command):
            return pattern
    return None


# ---------------------------------------------------------------------------
# 备份工具
# ---------------------------------------------------------------------------

def _timestamp() -> str:
    return str(int(time.time()))


def backup_file(path: str) -> Optional[str]:
    """把已存在的文件备份到 backups/<ts>/<相对路径>，返回备份路径；文件不存在返回 None。"""
    abs_path = os.path.abspath(path)
    if not os.path.exists(abs_path):
        return None
    ts = _timestamp()
    backup_root = os.path.join(config.BACKUPS_DIR, ts)
    # 用绝对路径去掉根斜杠作为相对路径，避免 .. 逃逸
    rel = abs_path.lstrip(os.sep)
    dest = os.path.join(backup_root, rel)
    os.makedirs(os.path.dirname(dest), exist_ok=True)
    shutil.copy2(abs_path, dest)
    return dest


# ---------------------------------------------------------------------------
# 动作执行
# ---------------------------------------------------------------------------

def _truncate(text: str, limit: int = 20000) -> tuple[str, bool]:
    if text is None:
        return "", False
    if len(text) <= limit:
        return text, False
    return text[:limit], True


def _kill_process_group(proc: subprocess.Popen) -> None:
    try:
        os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
    except Exception:
        try:
            proc.kill()
        except Exception:
            pass


def do_shell(command: str, timeout: Optional[int] = None) -> Dict[str, Any]:
    """执行 shell 命令，返回结果字典。"""
    danger = is_dangerous_command(command)
    if danger:
        return {
            "error": "命令被拒绝执行：检测到明显破坏性操作",
            "matched_pattern": danger,
            "command": command,
        }

    if timeout is None:
        timeout = config.CMD_TIMEOUT
    try:
        timeout = int(timeout)
    except (TypeError, ValueError):
        timeout = config.CMD_TIMEOUT
    if timeout <= 0:
        timeout = config.CMD_TIMEOUT

    try:
        proc = subprocess.Popen(
            command,
            shell=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            start_new_session=True,
            cwd=config.AGENT_ROOT,
            text=True,
            errors="replace",
        )
    except Exception as exc:  # noqa: BLE001
        return {"error": f"启动命令失败: {exc}"}

    # 记录当前子进程，供 /api/stop 使用
    try:
        from app import session as session_mod

        session_mod.set_current_process(proc)
    except Exception:
        pass

    timed_out = False
    try:
        stdout, stderr = proc.communicate(timeout=timeout)
    except subprocess.TimeoutExpired:
        timed_out = True
        _kill_process_group(proc)
        try:
            stdout, stderr = proc.communicate(timeout=5)
        except Exception:
            stdout, stderr = "", ""
    finally:
        try:
            from app import session as session_mod

            session_mod.clear_current_process(proc)
        except Exception:
            pass

    stdout = stdout or ""
    stderr = stderr or ""
    out_trunc, out_truncated = _truncate(stdout)
    err_trunc, err_truncated = _truncate(stderr)

    result: Dict[str, Any] = {
        "result_of": "shell",
        "command": command,
        "exit_code": proc.returncode,
        "stdout": out_trunc,
        "stderr": err_trunc,
        "truncated": bool(out_truncated or err_truncated),
    }
    if timed_out:
        result["timed_out"] = True
        result["timeout"] = timeout
    return result


def do_write_file(path: str, content: str) -> Dict[str, Any]:
    """写整个文件，自动建父目录，写前备份原文件。"""
    if not path:
        return {"error": "write_file 缺少 path"}
    if content is None:
        content = ""
    abs_path = os.path.abspath(path)
    backup_path = None
    try:
        backup_path = backup_file(abs_path)
        parent = os.path.dirname(abs_path)
        if parent:
            os.makedirs(parent, exist_ok=True)
        with open(abs_path, "w", encoding="utf-8") as fh:
            fh.write(content)
    except Exception as exc:  # noqa: BLE001
        return {"error": f"写入文件失败: {exc}", "path": abs_path}

    return {
        "result_of": "write_file",
        "path": abs_path,
        "bytes_written": len(content.encode("utf-8")),
        "backup": backup_path,
    }


def do_read_file(path: str, max_bytes: Optional[int] = None) -> Dict[str, Any]:
    """读取文件，超过 max_bytes 截断。"""
    if not path:
        return {"error": "read_file 缺少 path"}
    try:
        max_bytes = int(max_bytes) if max_bytes is not None else 20000
    except (TypeError, ValueError):
        max_bytes = 20000
    if max_bytes <= 0:
        max_bytes = 20000

    abs_path = os.path.abspath(path)
    if not os.path.exists(abs_path):
        return {"error": f"文件不存在: {abs_path}", "path": abs_path}
    if os.path.isdir(abs_path):
        return {"error": f"路径是目录而非文件: {abs_path}", "path": abs_path}

    try:
        with open(abs_path, "rb") as fh:
            raw = fh.read(max_bytes + 1)
    except Exception as exc:  # noqa: BLE001
        return {"error": f"读取文件失败: {exc}", "path": abs_path}

    truncated = len(raw) > max_bytes
    if truncated:
        raw = raw[:max_bytes]
    text = raw.decode("utf-8", errors="replace")
    return {
        "result_of": "read_file",
        "path": abs_path,
        "content": text,
        "bytes_read": len(raw),
        "truncated": truncated,
        "max_bytes": max_bytes,
    }


def do_self_restart(reason: str = "") -> Dict[str, Any]:
    """执行自更新流程。成功时进程会被 execv 替换，不会返回。"""
    from app import selfupdate

    try:
        selfupdate.self_restart(reason=reason)
    except Exception as exc:  # noqa: BLE001
        return {"error": f"self_restart 失败: {exc}", "reason": reason}
    # 正常情况下 perform_self_restart 会 execv，不会走到这里
    return {"error": "self_restart 未按预期重启进程", "reason": reason}


def do_done(message: str = "") -> Dict[str, Any]:
    return {"result_of": "done", "message": message or ""}


# ---------------------------------------------------------------------------
# 动作分发
# ---------------------------------------------------------------------------

def execute_action(action: Dict[str, Any]) -> Dict[str, Any]:
    """根据动作字典执行对应动作，返回结果字典。"""
    if not isinstance(action, dict):
        return {"error": "动作必须是 JSON 对象"}

    name = action.get("action")
    if not name:
        return {"error": "动作缺少 action 字段"}

    try:
        if name == "shell":
            return do_shell(action.get("command", ""), action.get("timeout"))
        if name == "write_file":
            return do_write_file(action.get("path", ""), action.get("content", ""))
        if name == "read_file":
            return do_read_file(action.get("path", ""), action.get("max_bytes"))
        if name == "self_restart":
            return do_self_restart(action.get("reason", ""))
        if name == "done":
            return do_done(action.get("message", ""))
    except Exception as exc:  # noqa: BLE001
        return {"error": f"执行动作 {name} 时异常: {exc}"}

    return {"error": f"未知动作: {name}"}


def parse_action(text: str) -> Optional[Dict[str, Any]]:
    """从模型输出中解析 JSON 动作。失败返回 None。"""
    if not text:
        return None
    text = text.strip()
    # 去掉可能的 markdown 代码围栏
    if text.startswith("```"):
        lines = text.splitlines()
        if lines and lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].strip().startswith("```"):
            lines = lines[:-1]
        text = "\n".join(lines).strip()

    try:
        obj = json.loads(text)
        if isinstance(obj, dict):
            return obj
    except json.JSONDecodeError:
        pass

    # 尝试提取第一个 {...} 块
    start = text.find("{")
    end = text.rfind("}")
    if start != -1 and end != -1 and end > start:
        candidate = text[start : end + 1]
        try:
            obj = json.loads(candidate)
            if isinstance(obj, dict):
                return obj
        except json.JSONDecodeError:
            return None
    return None