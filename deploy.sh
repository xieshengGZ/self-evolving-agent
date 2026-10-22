#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")"

if [ ! -f .env ]; then
    echo "错误: .env 不存在，请先创建（或运行 ./install.sh）" >&2
    exit 1
fi

exec ./install.sh
