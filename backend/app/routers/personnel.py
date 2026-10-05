"""人员定位接口：维护定位终端，覆盖记录离线、低电提醒、办理更换等动作。"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Query

from app.schemas import ActionResult, EntryPayload, PageResult
from app.services.personnel import PersonnelService

router = APIRouter(prefix="/api/personnel", tags=["人员定位"])

service = PersonnelService()

# LIST_FIELDS 是本表结构注册表：必须与 data/seed_manifest.json 中该表的 fields
# 以及 service 的 REQUIRED_FIELDS 对齐，改动后启动校验会直接失败。
LIST_FIELDS = ["终端编号", "携带人员", "所在位置", "入井时刻", "区域停留", "定位精度", "信号强度", "终端状态"]
STATUSES = ["在线", "离线", "低电量", "已更换"]


@router.get("", response_model=PageResult[dict])
def list_entries(
    keyword: str | None = Query(default=None, description="按终端编号检索"),
    status: str | None = Query(default=None, description="在线、离线、低电量、已更换"),
    page: int = 1,
    size: int = 20,
) -> PageResult[dict]:
    """按终端编号与状态过滤人员定位列表；没有数据时返回空页，不报错。"""
    if size > 200:
        raise HTTPException(status_code=400, detail="每页最多 200 条，请缩小分页范围")
    items, total = service.list_entries(keyword=keyword, status=status, page=page, size=size)
    return PageResult(items=items, total=total, page=page, size=size)


@router.get("/{entry_id}", response_model=dict)
def get_entry(entry_id: int) -> dict:
    """读取单条定位终端明细；不存在时给出可读的错误说明。"""
    entry = service.get_entry(entry_id)
    if entry is None:
        raise HTTPException(status_code=404, detail=f"定位终端 {entry_id} 不存在或已归档")
    return entry


@router.post("", response_model=ActionResult)
def create_entry(payload: EntryPayload) -> ActionResult:
    """登记一条定位终端，缺字段时说明原因而不是静默丢弃。"""
    entry, missing = service.create_entry(payload.values)
    if missing:
        return ActionResult(ok=False, message=f"缺少必填字段：{'、'.join(missing)}")
    return ActionResult(ok=True, message="定位终端已登记", entry=entry)


@router.post("/{entry_id}/actions", response_model=ActionResult)
def run_action(entry_id: int, payload: EntryPayload) -> ActionResult:
    """对单条定位终端执行记录离线、低电提醒、办理更换；不允许的动作会被拦下并说明原因。"""
    action = str(payload.values.get("action") or "").strip()
    entry, message = service.run_action(entry_id, action)
    if entry is None:
        return ActionResult(ok=False, message=message)
    return ActionResult(ok=True, message=message, entry=entry)


@router.get("/export")
def export_entries() -> dict[str, Any]:
    """导出人员定位清单：返回当前过滤条件下的全量数据。"""
    items, total = service.list_entries(page=1, size=10000)
    return {"module": "personnel", "total": total, "items": items}
