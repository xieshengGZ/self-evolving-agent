import os
import sys
import shutil
import time
import py_compile
import traceback

from app import config
from app import session


def _ts() -> str:
    return str(int(time.time()))


def _backup_dir(ts: str) -> str:
    return os.path.join(config.BACKUPS_DIR, ts)


def _iter_app_py_files():
    app_dir = os.path.join(config.AGENT_ROOT, "app")
    if not os.path.isdir(app_dir):
        return []
    result = []
    for name in sorted(os.listdir(app_dir)):
        if name.endswith(".py"):
            result.append(os.path.join(app_dir, name))
    return result


def _copy_into_backup(ts: str) -> None:
    dest_root = _backup_dir(ts)
    os.makedirs(dest_root, exist_ok=True)

    app_dir = os.path.join(config.AGENT_ROOT, "app")
    if os.path.isdir(app_dir):
        for name in sorted(os.listdir(app_dir)):
            if name.endswith(".py"):
                src = os.path.join(app_dir, name)
                dst = os.path.join(dest_root, "app", name)
                os.makedirs(os.path.dirname(dst), exist_ok=True)
                shutil.copy2(src, dst)

    prompt_src = os.path.join(config.AGENT_ROOT, "prompts", "system.md")
    if os.path.isfile(prompt_src):
        prompt_dst = os.path.join(dest_root, "prompts", "system.md")
        os.makedirs(os.path.dirname(prompt_dst), exist_ok=True)
        shutil.copy2(prompt_src, prompt_dst)


def _restore_from_backup(ts: str) -> None:
    src_root = _backup_dir(ts)
    if not os.path.isdir(src_root):
        raise RuntimeError("备份目录不存在: %s" % src_root)

    app_backup = os.path.join(src_root, "app")
    if os.path.isdir(app_backup):
        app_dir = os.path.join(config.AGENT_ROOT, "app")
        os.makedirs(app_dir, exist_ok=True)
        for name in sorted(os.listdir(app_backup)):
            if name.endswith(".py"):
                shutil.copy2(
                    os.path.join(app_backup, name),
                    os.path.join(app_dir, name),
                )

    prompt_backup = os.path.join(src_root, "prompts", "system.md")
    if os.path.isfile(prompt_backup):
        prompt_dir = os.path.join(config.AGENT_ROOT, "prompts")
        os.makedirs(prompt_dir, exist_ok=True)
        shutil.copy2(prompt_backup, os.path.join(prompt_dir, "system.md"))


def _compile_check() -> None:
    errors = []
    for path in _iter_app_py_files():
        try:
            py_compile.compile(path, doraise=True)
        except py_compile.PyCompileError as exc:
            errors.append("%s: %s" % (os.path.basename(path), exc.msg))
        except Exception as exc:  # noqa: BLE001
            errors.append("%s: %s" % (os.path.basename(path), str(exc)))
    if errors:
        raise RuntimeError("语法检查失败:\n" + "\n".join(errors))


def _write_restart_log(ts: str, reason: str) -> None:
    os.makedirs(config.STATE_DIR, exist_ok=True)
    log_path = os.path.join(config.STATE_DIR, "last_restart.log")
    line = "[%s] self_restart reason=%s backup=%s\n" % (
        time.strftime("%Y-%m-%d %H:%M:%S"),
        config.redact(reason),
        ts,
    )
    with open(log_path, "a", encoding="utf-8") as fh:
        fh.write(line)


def _exec_restart() -> None:
    # execv 会继承当前环境；先重新读取 .env，避免改完配置后自我重启仍用旧值。
    try:
        from app.env import load_dotenv

        load_dotenv(override=True)
    except Exception:  # noqa: BLE001
        pass
    os.execv(
        sys.executable,
        [sys.executable, "-m", "app.main"],
    )


def self_restart(reason: str = "") -> None:
    """执行自更新流程。成功时进程被 execv 替换，不返回；失败抛异常。"""
    ts = _ts()
    _copy_into_backup(ts)

    try:
        _compile_check()
    except Exception:
        try:
            _restore_from_backup(ts)
        except Exception:  # noqa: BLE001
            pass
        raise

    try:
        session.save()
    except Exception:  # noqa: BLE001
        pass

    _write_restart_log(ts, reason)

    try:
        session.add_event("info", "self_restart: 备份 %s 完成，语法检查通过，准备重启" % ts)
    except Exception:  # noqa: BLE001
        pass

    _exec_restart()


def rollback_latest() -> None:
    """从 backups/latest 恢复并重启。"""
    if not os.path.isdir(config.BACKUPS_DIR):
        raise RuntimeError("备份目录不存在")

    candidates = [
        name
        for name in os.listdir(config.BACKUPS_DIR)
        if os.path.isdir(os.path.join(config.BACKUPS_DIR, name)) and name.isdigit()
    ]
    if not candidates:
        raise RuntimeError("没有可用备份")

    latest = sorted(candidates, key=lambda x: int(x))[-1]
    _restore_from_backup(latest)

    try:
        _compile_check()
    except Exception:
        raise

    try:
        session.save()
    except Exception:  # noqa: BLE001
        pass

    _write_restart_log(latest, "rollback")
    session.add_event("info", "rollback: 已从备份 %s 恢复，准备重启" % latest)

    _exec_restart()


def safe_self_restart(reason: str = "") -> dict:
    """供动作层调用：成功不返回（execv），失败返回错误 dict。"""
    try:
        self_restart(reason)
    except Exception as exc:  # noqa: BLE001
        return {
            "error": "self_restart 失败: %s" % config.redact(str(exc)),
            "traceback": config.redact(traceback.format_exc()),
        }
    return {"error": "self_restart 未生效（不应到达此处）"}