#!/usr/bin/env bash
# 开发模式启动（自动重载）。首次运行会自动 uv sync 装依赖。
set -euo pipefail
cd "$(dirname "$0")"

uv sync
echo "==> http://localhost:8000/ask?q=你好"
exec uv run fastapi dev "$@"
