"""内存数据仓库：保存按初始化清单装载的各业务模块数据。

真实项目里这里会换成数据库访问层；当前实现只依赖标准库，保证克隆下来就能起。
数据不在此模块构造，进程启动时由 ``app.seed_loader.bootstrap`` 校验清单后整体
换入（``replace_tables``）：清单有问题时启动直接失败，绝不会出现半张表的数据。
"""
from __future__ import annotations

import copy
from typing import Any


class Store:
    def __init__(self) -> None:
        self._tables: dict[str, list[dict[str, Any]]] = {}
        # 由 bootstrap 在成功装载后写入：manifest（清单）或 snapshot（上一次成功快照）。
        self.loaded_from: str = "unloaded"

    def is_loaded(self) -> bool:
        return self.loaded_from != "unloaded"

    def replace_tables(self, tables: dict[str, list[dict[str, Any]]]) -> None:
        """整体换入一套已校验通过的表数据（深拷贝，事务式：失败就不调用本方法）。"""
        self._tables = {name: copy.deepcopy(rows) for name, rows in tables.items()}

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
