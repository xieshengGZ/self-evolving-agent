import os
import sys


def _env(name: str, default: str | None = None, required: bool = False) -> str:
    value = os.environ.get(name, default)
    if required and (value is None or value == ""):
        sys.stderr.write(f"[config] missing required environment variable: {name}\n")
        sys.exit(1)
    return value if value is not None else ""


DEEPSEEK_API_KEY: str = _env("DEEPSEEK_API_KEY", required=True)
DEEPSEEK_BASE_URL: str = _env("DEEPSEEK_BASE_URL", "https://api.deepseek.com")
DEEPSEEK_MODEL: str = _env("DEEPSEEK_MODEL", "deepseek-chat")

AGENT_WEB_TOKEN: str = _env("AGENT_WEB_TOKEN", required=True)
AGENT_PORT: int = int(_env("AGENT_PORT", "8088"))
AGENT_ROOT: str = os.path.abspath(_env("AGENT_ROOT", os.getcwd()))
AGENT_MAX_STEPS: int = int(_env("AGENT_MAX_STEPS", "20"))
AGENT_CMD_TIMEOUT: int = int(_env("AGENT_CMD_TIMEOUT", "120"))

APP_DIR: str = os.path.join(AGENT_ROOT, "app")
PLUGINS_DIR: str = os.path.join(AGENT_ROOT, "plugins")
PROMPTS_DIR: str = os.path.join(AGENT_ROOT, "prompts")
SYSTEM_PROMPT_PATH: str = os.path.join(PROMPTS_DIR, "system.md")
STATE_DIR: str = os.path.join(AGENT_ROOT, "state")
BACKUPS_DIR: str = os.path.join(AGENT_ROOT, "backups")
DB_PATH: str = os.path.abspath(_env("AGENT_DB_PATH", os.path.join(STATE_DIR, "agent.db")))
SESSION_PATH: str = os.path.join(STATE_DIR, "session.json")
LAST_RESTART_LOG: str = os.path.join(STATE_DIR, "last_restart.log")
SERVICE_LOG: str = os.path.join(STATE_DIR, "service.log")
STATIC_DIR: str = os.path.join(APP_DIR, "static")

SECRET_VALUES: list[str] = [v for v in (DEEPSEEK_API_KEY, AGENT_WEB_TOKEN) if v]


def ensure_dirs() -> None:
    for d in (STATE_DIR, BACKUPS_DIR):
        os.makedirs(d, exist_ok=True)


def redact(text: str) -> str:
    if not text:
        return text
    out = text
    for secret in SECRET_VALUES:
        if secret and secret in out:
            out = out.replace(secret, "***REDACTED***")
    return out

# ---- self-heal limits (added) ----
AGENT_MAX_CONSECUTIVE_FAILURES: int = int(_env("AGENT_MAX_CONSECUTIVE_FAILURES", "3"))
AGENT_MAX_RUNTIME: int = int(_env("AGENT_MAX_RUNTIME", "600"))
AGENT_MAX_RESTARTS_PER_WINDOW: int = int(_env("AGENT_MAX_RESTARTS_PER_WINDOW", "5"))
AGENT_RESTART_WINDOW_SEC: int = int(_env("AGENT_RESTART_WINDOW_SEC", "120"))
AGENT_ENABLE_PLANNER: int = int(_env("AGENT_ENABLE_PLANNER", "1"))
AGENT_PLUGIN_AUTORELOAD: int = int(_env("AGENT_PLUGIN_AUTORELOAD", "0"))
