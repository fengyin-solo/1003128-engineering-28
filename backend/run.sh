#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")"
# .venv 存在但解释器不可用（比如从别的机器拷来的）时重建，保证一次就能起
if [ ! -x .venv/bin/python ]; then
  rm -rf .venv
  python3 -m venv .venv
fi
.venv/bin/pip install -q -r requirements.txt
# 启动前先校验示例数据清单，清单坏了在这里就能看到是哪张表
.venv/bin/python -m app.seed_loader
exec .venv/bin/uvicorn app.main:app --host 127.0.0.1 --port 8000
