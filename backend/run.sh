#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")"
if [ ! -d .venv ]; then
  python3 -m venv .venv
fi
.venv/bin/pip install -q -r requirements.txt
# 起服务前先校验初始化清单：坏清单在这里就非零退出，不进入端口监听
#（uvicorn 的 lifespan 里还有同样的校验，双保险）。
.venv/bin/python -m app.seed_loader --check
exec .venv/bin/uvicorn app.main:app --host 127.0.0.1 --port 8000
