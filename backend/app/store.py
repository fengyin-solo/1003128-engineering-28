"""内存数据仓库：保存示例数据并给运营概览提供统计。

示例数据不在代码里写死：进程启动时由 app.seed_loader 按清单
（seed/manifest.yaml）装载进来，这里只负责保存与查询。真实项目里这里会
换成数据库访问层；当前实现只依赖标准库，保证克隆下来就能起。
"""
from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from app.seed_loader import LoadReport


class Store:
    def __init__(self) -> None:
        self._tables: dict[str, list[dict[str, Any]]] = {}
        # 最近一次装载的结果（来源、条数、是否回退），由 seed_loader 写入
        self.seed_report: LoadReport | None = None

    def load(self, tables: dict[str, list[dict[str, Any]]]) -> None:
        """整体替换内存表。调用方保证 tables 已通过校验，替换本身一步到位。"""
        self._tables = {
            name: [dict(row) for row in rows] for name, rows in tables.items()
        }

    def module_names(self) -> list[str]:
        return sorted(self._tables)

    def rows(self, module: str) -> list[dict[str, Any]]:
        return self._tables.setdefault(module, [])

    def find(self, module: str, entry_id: int) -> dict[str, Any] | None:
        for row in self.rows(module):
            if int(row.get("id", 0)) == entry_id:
                return row
        return None

    def overview(self) -> dict[str, object]:
        modules: list[dict[str, object]] = []
        for name in self.module_names():
            rows = self.rows(name)
            modules.append({
                "name": name,
                "created": len(rows),
                "pending": sum(1 for row in rows if row.get("pending")),
                "abnormal": sum(1 for row in rows if row.get("abnormal")),
            })
        cards = [
            {"label": "业务模块", "value": len(modules)},
            {"label": "今日新增", "value": sum(int(item["created"]) for item in modules)},
            {"label": "待处理", "value": sum(int(item["pending"]) for item in modules)},
            {"label": "异常量", "value": sum(int(item["abnormal"]) for item in modules)},
        ]
        return {"cards": cards, "modules": modules}


store = Store()
