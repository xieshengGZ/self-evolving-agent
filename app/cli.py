"""Small operator CLI: status / map / handoff / reload.

This exists so a future session can learn the project's current state without
replaying chat history.
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path
from typing import List

from app.env import load_dotenv as _load_dotenv

ROOT = Path(__file__).resolve().parents[1]


def _git_sha() -> str:
    try:
        out = subprocess.run(
            ["git", "-C", str(ROOT), "rev-parse", "--short", "HEAD"],
            capture_output=True,
            text=True,
            check=True,
            timeout=5,
        )
        return out.stdout.strip() or "unknown"
    except Exception:  # noqa: BLE001
        return "unknown"


def _service_state() -> str:
    try:
        out = subprocess.run(
            ["systemctl", "is-active", "agent-services"],
            capture_output=True,
            text=True,
            timeout=5,
        )
        return out.stdout.strip() or "unknown"
    except Exception:  # noqa: BLE001
        return "unknown"


def _section(path: Path, heading: str) -> List[str]:
    if not path.is_file():
        return []
    lines = path.read_text(encoding="utf-8").splitlines()
    collected: List[str] = []
    active = False
    for line in lines:
        if line.startswith("## "):
            if active:
                break
            active = heading in line
            continue
        if active and line.strip():
            collected.append(line.rstrip())
    return collected


def _runtime():
    from app.kernel import get_runtime

    runtime = get_runtime()
    runtime.plugins.load_all()
    return runtime


def _print_plugins(runtime) -> None:
    for plugin in runtime.plugins.list_plugins():
        tools = ", ".join(plugin["tools"]) or "-"
        ops = ", ".join(plugin["http"]) or "-"
        print(f"  - {plugin['name']} {plugin['version']} [{plugin['status']}]")
        print(f"      tools: {tools}")
        print(f"      http : {ops}")

    errors = runtime.plugins.errors()
    if errors:
        print("  errors:")
        for name, message in errors.items():
            print(f"      {name}: {message}")


def cmd_status() -> int:
    _load_dotenv()
    from app import config
    from app.storage import Storage

    runtime = _runtime()
    print(f"project : {ROOT}")
    print(f"git     : {_git_sha()}")
    print(f"service : {_service_state()}")
    print(f"root    : {config.AGENT_ROOT}")
    print(f"plugins : {len(runtime.plugins.list_plugins())}")
    _print_plugins(runtime)
    print(f"tools   : {', '.join(runtime.plugins.tool_names()) or '-'}")

    db = Storage()
    print(f"db      : {db.path}")
    data = None
    if db.path.exists():
        try:
            data = db.load_current()
        except Exception:  # noqa: BLE001
            data = None
    if data:
        print(f"session : status={data['status']} conv={data['conv_id'] or '-'}")
        print(f"task    : {data['task'] or '-'}")
        print(f"rows    : {db.totals()}")
    else:
        print("session : empty (run: python -m app.migrate)")

    next_lines = _section(ROOT / "docs" / "STATUS.md", "下一步")
    if next_lines:
        print("next    :")
        for line in next_lines:
            print(f"  {line}")
    return 0


def cmd_map() -> int:
    _load_dotenv()
    runtime = _runtime()
    for plugin in runtime.plugins.list_plugins():
        print(f"{plugin['name']} {plugin['version']} [{plugin['status']}]")
        print(f"  tools  : {', '.join(plugin['tools']) or '-'}")
        print(f"  http   : {', '.join(plugin['http']) or '-'}")
        print(f"  hooks  : {plugin['hooks'] or '-'}")
    return 0


def cmd_handoff() -> int:
    _load_dotenv()
    from app import config
    from app.storage import Storage

    cmd_status()
    print("recent  :")
    db = Storage()
    events = []
    if db.path.exists():
        try:
            events = (db.load_current() or {}).get("events", [])
        except Exception:  # noqa: BLE001
            events = []
    for event in events[-5:]:
        message = config.redact(str(event.get("message", "")))
        print(f"  [{event.get('kind')}] {message[:160]}")
    print("read    : AGENTS.md -> docs/STATUS.md -> docs/ARCHITECTURE.md")
    return 0


def cmd_reload(name: str) -> int:
    _load_dotenv()
    runtime = _runtime()
    try:
        loaded = runtime.plugins.reload(name)
    except Exception as exc:  # noqa: BLE001
        print(f"reload failed: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1
    print(
        f"reloaded {loaded.manifest.name} {loaded.manifest.version} "
        f"tools={loaded.layer.tools.names()} http={loaded.layer.http.ops()}"
    )
    return 0


def cmd_migrate() -> int:
    _load_dotenv()
    from app import config
    from app.storage import Storage

    config.ensure_dirs()
    storage = Storage()
    result = storage.migrate_json()
    print(f"migrated : {result.get('migrated')}")
    print(f"source   : {result.get('source')}")
    if result.get("reason"):
        print(f"reason   : {result['reason']}")
    print(f"rows     : {storage.totals()}")
    return 0


def main(argv: List[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if not args or args[0] == "status":
        return cmd_status()
    if args[0] == "map":
        return cmd_map()
    if args[0] == "handoff":
        return cmd_handoff()
    if args[0] == "migrate":
        return cmd_migrate()
    if args[0] == "reload" and len(args) == 2:
        return cmd_reload(args[1])
    print(
        "usage: python -m app.cli [status|map|handoff|migrate|reload <plugin>]",
        file=sys.stderr,
    )
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
