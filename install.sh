#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$ROOT"

# If invoked via sudo from a normal account, drop back to that account so the
# venv, .env and state files are owned by the service user. A direct root login
# is allowed, but the service will run as root (with a warning).
if [ "${EUID}" -eq 0 ] && [ -n "${SUDO_USER:-}" ] && [ "${SUDO_USER}" != "root" ]; then
    echo "Dropping privileges to ${SUDO_USER} for the service files."
    exec sudo -u "${SUDO_USER}" -- "$0" "$@"
fi

if [ "${EUID}" -eq 0 ]; then
    SERVICE_USER="root"
    SERVICE_GROUP="root"
    echo "WARNING: installing as root; the agent service will run with root privileges."
    echo "         Use a normal user + sudo if you want a non-root service."
else
    SERVICE_USER="$(id -un)"
    SERVICE_GROUP="$(id -gn)"
fi

for cmd in python3 systemctl sudo; do
    if ! command -v "$cmd" >/dev/null 2>&1; then
        echo "Missing required command: $cmd" >&2
        exit 1
    fi
done

if ! python3 -c 'import venv' >/dev/null 2>&1; then
    echo "python3-venv is missing. Install it first: sudo apt install python3-venv" >&2
    exit 1
fi

case "$ROOT" in
    *" "*) echo "The repository path must not contain spaces: $ROOT" >&2; exit 1 ;;
esac

if [ ! -f .env ]; then
    if [ ! -t 0 ]; then
        echo "No .env and no interactive terminal. Copy .env.example, fill it, then rerun." >&2
        exit 1
    fi
    echo "First install: enter your DeepSeek API key (input is hidden)."
    read -rsp "DEEPSEEK_API_KEY: " DS_KEY </dev/tty
    echo
    if [ -z "$DS_KEY" ]; then
        echo "DEEPSEEK_API_KEY cannot be empty." >&2
        exit 1
    fi
    TOKEN="$(python3 -c 'import secrets; print(secrets.token_hex(32))')"
    umask 077
    {
        printf 'DEEPSEEK_API_KEY=%s\n' "$DS_KEY"
        printf 'AGENT_WEB_TOKEN=%s\n' "$TOKEN"
        sed -n '4,$p' .env.example
    } > .env
    chmod 600 .env
    echo "Wrote .env with a generated web token."
fi

if grep -q '^DEEPSEEK_API_KEY=sk-your-key-here$' .env; then
    echo ".env still contains the placeholder API key; edit it before installing." >&2
    exit 1
fi
if grep -q '^AGENT_WEB_TOKEN=change-me' .env; then
    echo ".env still contains a placeholder AGENT_WEB_TOKEN; generate a random one." >&2
    exit 1
fi

if [ ! -x .venv/bin/python ]; then
    python3 -m venv .venv
fi
.venv/bin/pip install -q -r requirements.txt

mkdir -p state
touch state/service.log

UNIT="$(mktemp)"
trap 'rm -f "$UNIT"' EXIT
cat > "$UNIT" <<UNIT_EOF
[Unit]
Description=Self-Evolving Agent daemon
After=network-online.target
Wants=network-online.target
StartLimitIntervalSec=300
StartLimitBurst=10

[Service]
Type=simple
User=$SERVICE_USER
Group=$SERVICE_GROUP
WorkingDirectory=$ROOT
EnvironmentFile=$ROOT/.env
ExecStart=$ROOT/.venv/bin/python -m app.main
Restart=on-failure
RestartSec=3
TimeoutStopSec=15
KillMode=mixed
SyslogIdentifier=agent-services

StandardOutput=append:$ROOT/state/service.log
StandardError=append:$ROOT/state/service.log

NoNewPrivileges=false
PrivateTmp=false
ProtectSystem=false
ProtectHome=false

[Install]
WantedBy=multi-user.target
UNIT_EOF

LOGROTATE="$(mktemp)"
cat > "$LOGROTATE" <<LOGROTATE_EOF
$ROOT/state/service.log {
    daily
    rotate 7
    compress
    delaycompress
    missingok
    notifempty
    copytruncate
    su $SERVICE_USER $SERVICE_GROUP
}
LOGROTATE_EOF

sudo install -m 0644 "$UNIT" /etc/systemd/system/agent-services.service
sudo install -m 0644 "$LOGROTATE" /etc/logrotate.d/agent-services
rm -f "$UNIT" "$LOGROTATE"
trap - EXIT
sudo systemctl daemon-reload
sudo systemctl enable agent-services >/dev/null
sudo systemctl restart agent-services

sleep 2
if ! systemctl is-active --quiet agent-services; then
    echo "agent-services failed to start. Recent logs:" >&2
    sudo journalctl -u agent-services -n 40 --no-pager >&2 || true
    exit 1
fi

PORT="$(grep -E '^AGENT_PORT=' .env | tail -n 1 | cut -d= -f2 || true)"
PORT="${PORT:-8088}"
IP="$(hostname -I 2>/dev/null | awk '{print $1}' || true)"
IP="${IP:-127.0.0.1}"

echo
echo "agent-services is running."
echo "URL   : http://$IP:$PORT"
echo "Token : $ROOT/.env (AGENT_WEB_TOKEN)"
echo "Logs  : sudo journalctl -u agent-services -f"
