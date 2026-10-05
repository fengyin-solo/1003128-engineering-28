"""矿区台账接口：维护矿区，覆盖停产整顿、恢复生产、闭坑登记等动作。"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Query

from app.schemas import ActionResult, EntryPayload, PageResult
from app.services.minearea import MineareaService

router = APIRouter(prefix="/api/minearea", tags=["矿区台账"])

service = MineareaService()

# LIST_FIELDS 是本表结构注册表：必须与 data/seed_manifest.json 中该表的 fields
# 以及 service 的 REQUIRED_FIELDS 对齐，改动后启动校验会直接失败。
LIST_FIELDS = ["矿区编号", "矿区名称", "开采矿种", "核定产能", "开采方式", "服务年限", "安全等级", "矿区状态"]
STATUSES = ["正常生产", "停产整顿", "检修中", "已闭坑"]


@router.get("", response_model=PageResult[dict])
def list_entries(
    keyword: str | None = Query(default=None, description="按矿区编号检索"),
    status: str | None = Query(default=None, description="正常生产、停产整顿、检修中、已闭坑"),
    page: int = 1,
    size: int = 20,
) -> PageResult[dict]:
    """按矿区编号与状态过滤矿区台账列表；没有数据时返回空页，不报错。"""
    if size > 200:
        raise HTTPException(status_code=400, detail="每页最多 200 条，请缩小分页范围")
    items, total = service.list_entries(keyword=keyword, status=status, page=page, size=size)
    return PageResult(items=items, total=total, page=page, size=size)


@router.get("/{entry_id}", response_model=dict)
def get_entry(entry_id: int) -> dict:
    """读取单条矿区明细；不存在时给出可读的错误说明。"""
    entry = service.get_entry(entry_id)
    if entry is None:
        raise HTTPException(status_code=404, detail=f"矿区 {entry_id} 不存在或已归档")
    return entry


@router.post("", response_model=ActionResult)
def create_entry(payload: EntryPayload) -> ActionResult:
    """登记一条矿区，缺字段时说明原因而不是静默丢弃。"""
    entry, missing = service.create_entry(payload.values)
    if missing:
        return ActionResult(ok=False, message=f"缺少必填字段：{'、'.join(missing)}")
    return ActionResult(ok=True, message="矿区已登记", entry=entry)


@router.post("/{entry_id}/actions", response_model=ActionResult)
def run_action(entry_id: int, payload: EntryPayload) -> ActionResult:
    """对单条矿区执行停产整顿、恢复生产、闭坑登记；不允许的动作会被拦下并说明原因。"""
    action = str(payload.values.get("action") or "").strip()
    entry, message = service.run_action(entry_id, action)
    if entry is None:
        return ActionResult(ok=False, message=message)
    return ActionResult(ok=True, message=message, entry=entry)


@router.get("/export")
def export_entries() -> dict[str, Any]:
    """导出矿区台账清单：返回当前过滤条件下的全量数据。"""
    items, total = service.list_entries(page=1, size=10000)
    return {"module": "minearea", "total": total, "items": items}
