#!/bin/bash
# 给 AI 助手 / 脚本用的启动器：自动进入项目目录、使用项目的 venv，再调用 dy_cli.py
# 用法： ./dy.sh comments --url <作品链接> --max 100
cd "$(dirname "$0")" || exit 1
if [ -x venv/bin/python ]; then PY=venv/bin/python; else PY=python3; fi
exec "$PY" dy_cli.py "$@"
