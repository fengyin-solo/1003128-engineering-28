"""矿山安全监测管理平台 后端服务入口。

启动：uvicorn app.main:app --host 127.0.0.1 --port 8000
健康检查：GET /api/health

初始化顺序（见 docs/seed.md）：config -> 结构注册表(routers/services) ->
加载并校验 data/seed_manifest.json -> 整体换入内存仓库并写 last-good 快照 -> 对外服务。
清单任何问题都会在监听端口之前抛出，进程非零退出，不对外提供半成品数据。
"""
from __future__ import annotations

import contextlib
import logging

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.config import settings
from app.routers import ROUTERS
from app.seed_loader import ManifestError, bootstrap, load_manifest_tables, save_snapshot
from app.store import store

logger = logging.getLogger("app")


@contextlib.asynccontextmanager
async def lifespan(_: FastAPI):
    # 启动阶段装载：失败直接向上抛，uvicorn 中止启动（非零退出），现役数据不受影响。
    bootstrap(store)
    yield


app = FastAPI(title="矿山安全监测管理平台", version="1.0.0", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.allowed_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

for module in ROUTERS:
    app.include_router(module.router)


@app.get("/api/health")
def health() -> dict[str, object]:
    """健康检查：确认服务已经监听、初始化数据已经按清单装载就绪。"""
    return {
        "ok": True,
        "app": settings.app_name,
        "env": settings.env,
        "modules": len(store.module_names()),
        "seed": store.loaded_from,
        "total": sum(len(store.rows(name)) for name in store.module_names()),
    }


@app.get("/api/overview")
def overview() -> dict[str, object]:
    """运营概览：直接统计内存仓库里由清单装载的记录，看板数字与初始化数据同源。"""
    return store.overview()


@app.post("/api/seed/reload")
def reload_seed() -> dict[str, object]:
    """修好清单后不用重启进程：重新按清单装载。

    装载仍是事务式的——校验不过时保留上一次装载的数据继续服务，并把问题逐条返回。
    """
    try:
        tables, meta = load_manifest_tables()
    except ManifestError as exc:
        return {"ok": False, "reloaded": False, "errors": exc.errors,
                "message": "清单仍有问题，已保留上一次装载的数据"}
    store.replace_tables(tables)
    store.loaded_from = "manifest"
    save_snapshot(tables, meta)
    return {"ok": True, "reloaded": True, "modules": len(tables), "total": meta["total"]}
