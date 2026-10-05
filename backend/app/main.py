"""矿山安全监测管理平台 后端服务入口。

启动：uvicorn app.main:app --host 127.0.0.1 --port 8000
健康检查：GET /api/health
"""
from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from typing import AsyncIterator

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware

from app.config import settings
from app.routers import ROUTERS
from app.seed_loader import ManifestError, bootstrap_store, reload_store
from app.store import store

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    # 启动时装载示例数据：清单校验失败会在这里直接抛出，进程起不来；
    # 有上一次成功装载的快照时回退到快照，并以 degraded 状态继续运行。
    bootstrap_store(store, settings)
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
    """健康检查：确认服务已经监听、示例数据已经就绪。"""
    return {
        "ok": True,
        "app": settings.app_name,
        "modules": len(store.module_names()),
        "seed": store.seed_report.as_dict() if store.seed_report else {},
    }


@app.get("/api/overview")
def overview() -> dict[str, object]:
    """运营概览：把各业务模块的待处理量汇总成看板卡片。"""
    return store.overview()


@app.post("/api/admin/seed/reload")
def reload_seed() -> dict[str, object]:
    """修好清单后重试装载：校验失败返回 422 并指出是哪一张表，内存数据保持不动。"""
    try:
        report = reload_store(store, settings)
    except ManifestError as exc:
        raise HTTPException(
            status_code=422,
            detail={
                "message": "示例数据清单校验失败，已保留当前数据",
                "problems": exc.problems,
            },
        ) from exc
    return {
        "ok": True,
        "message": f"示例数据已重新装载：{report.modules} 个模块、{report.records} 条记录",
        "seed": report.as_dict(),
    }
