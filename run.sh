#!/usr/bin/env bash
# 一键启动：初始化 SQLite，生成材质冲突/批量隔离/维修样例，启动系统
set -euo pipefail
cd "$(dirname "$0")"
exec python3 -m app.server --demo --host 127.0.0.1 --port "${PORT:-8000}"
