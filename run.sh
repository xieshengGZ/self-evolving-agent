#!/usr/bin/env bash
# 手动前台启动用；systemd 下不要用它
set -u
cd "$(dirname "$0")"
exec .venv/bin/python -m app.main
