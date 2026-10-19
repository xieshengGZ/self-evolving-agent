#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")"

if [ ! -f .env ]; then
    echo "错误: .env 不存在，请先创建" >&2
    exit 1
fi

mkdir -p state
touch state/service.log
chown ubuntu:ubuntu state/service.log

sudo cp agent-services.service /etc/systemd/system/agent-services.service
sudo cp agent-services.logrotate /etc/logrotate.d/agent-services
sudo chmod 644 /etc/systemd/system/agent-services.service
sudo chmod 644 /etc/logrotate.d/agent-services
sudo chown root:root /etc/systemd/system/agent-services.service /etc/logrotate.d/agent-services

sudo systemctl daemon-reload
sudo systemctl enable agent-services >/dev/null
sudo systemctl restart agent-services
sleep 2

echo "=== systemctl status ==="
sudo systemctl --no-pager --lines=8 status agent-services || true
echo
echo "=== 日志文件 ==="
ls -lh state/service.log
echo
echo "=== 实时日志: tail -f state/service.log ==="
