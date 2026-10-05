"""示例数据清单的装载、校验与回退。

`seed/manifest.yaml` 是示例数据的唯一来源：按模块声明字段与初始记录，
进程启动时由本模块装载进内存仓库（见 `app/main.py` 的 lifespan）。

装载规则：

- 校验清单与代码里各路由的 LIST_FIELDS 是否一一对应：缺模块、字段对不上、
  记录缺字段等都会抛出 ManifestError，错误里逐条指出是哪一张表、什么问题，
  不允许静默跳过。
- 装载是原子的：全部校验通过后才整体替换内存数据，失败不会动已有数据。
- 每次成功装载会把结果写入快照文件；下次启动遇到清单损坏时回退到上一次
  成功装载的数据，并以 degraded 状态继续运行（健康检查可见）；连快照也
  没有时启动直接失败。
- 数据量由 SEED_SCALE 控制：各环境只允许条数不同，结构永远以同一份清单为准。
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import sys
import tempfile
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING, Any

import yaml

if TYPE_CHECKING:
    from app.config import Settings
    from app.store import Store

logger = logging.getLogger("app.seed")

MANIFEST_VERSION = 1
# SEED_SCALE 放大时每批记录的 id 偏移，保证批次之间 id 不冲突
ID_STRIDE = 1_000_000
# 记录 values 里允许的值类型；日期等请加引号写成字符串
ALLOWED_VALUE_TYPES = (str, int, float, bool, type(None))
RECORD_KEYS = {"id", "status", "pending", "abnormal", "values"}
MODULE_KEYS = {"label", "fields", "records"}


class ManifestError(Exception):
    """清单校验失败。problems 逐条列出是哪一张表、什么问题。"""

    def __init__(self, problems: list[str]) -> None:
        self.problems = problems
        message = "示例数据清单校验失败：\n" + "\n".join(f"  - {p}" for p in problems)
        super().__init__(message)


@dataclass(frozen=True)
class ModuleSeed:
    """一个模块在清单里的声明：中文名、字段列表与初始记录。"""

    label: str
    fields: list[str]
    records: list[dict[str, Any]]


@dataclass
class LoadReport:
    """一次装载的结果，挂在 store.seed_report 上供健康检查读取。"""

    source: str  # "manifest" 或 "snapshot"
    path: str
    scale: int
    modules: int
    records: int
    degraded: bool = False
    problems: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return {
            "source": self.source,
            "path": self.path,
            "scale": self.scale,
            "modules": self.modules,
            "records": self.records,
            "degraded": self.degraded,
            "problems": self.problems,
        }


def expected_modules() -> dict[str, list[str]]:
    """代码侧登记的模块与字段，以各路由的 LIST_FIELDS 为准。

    延迟导入 routers：routers → services → store，本模块不能再被 store 引用回去。
    """
    from app.routers import ROUTERS

    expected: dict[str, list[str]] = {}
    for module in ROUTERS:
        name = module.router.prefix.rsplit("/", 1)[-1]
        expected[name] = list(module.LIST_FIELDS)
    return expected


def validate_document(
    doc: Any, expected: dict[str, list[str]] | None = None
) -> dict[str, ModuleSeed]:
    """校验清单文档并返回各模块的种子声明；任何问题都汇进 ManifestError 一次抛出。"""
    if expected is None:
        expected = expected_modules()
    problems: list[str] = []

    if not isinstance(doc, dict):
        raise ManifestError(["清单顶层必须是键值结构（version + modules）"])
    version = doc.get("version")
    if version != MANIFEST_VERSION:
        problems.append(f"清单 version 必须是 {MANIFEST_VERSION}，当前是 {version!r}")
    for key in sorted(set(doc) - {"version", "modules"}):
        problems.append(f"清单顶层有不认识的键「{key}」")
    modules = doc.get("modules")
    if not isinstance(modules, dict) or not modules:
        problems.append("清单缺少 modules 段，或 modules 不是键值结构")
        raise ManifestError(problems)

    for name in sorted(set(expected) - set(modules)):
        problems.append(f"缺少模块 {name}：代码里注册了这张表，清单里没有声明")
    for name in sorted(set(modules) - set(expected)):
        problems.append(f"模块 {name}：清单声明了这张表，但代码里没有注册")

    seeds: dict[str, ModuleSeed] = {}
    for name in sorted(set(expected) & set(modules)):
        seeds[name] = _validate_module(name, modules[name], expected[name], problems)

    if problems:
        raise ManifestError(problems)
    return seeds


def _validate_module(
    name: str, spec: Any, expected_fields: list[str], problems: list[str]
) -> ModuleSeed:
    """校验单个模块的声明；问题追加到 problems，返回值只在整份清单零问题时才会被使用。"""
    if not isinstance(spec, dict):
        problems.append(f"表 {name}：声明必须是 label/fields/records 键值结构")
        return ModuleSeed(label=name, fields=[], records=[])

    label = spec.get("label")
    if not isinstance(label, str) or not label.strip():
        problems.append(f"表 {name}：缺少 label（模块中文名）")
        label = name
    where = f"表 {name}（{label}）"

    for key in sorted(set(spec) - MODULE_KEYS):
        problems.append(f"{where}：有不认识的键「{key}」")

    fields = spec.get("fields")
    if not (
        isinstance(fields, list)
        and fields
        and all(isinstance(f, str) and f.strip() for f in fields)
    ):
        problems.append(f"{where}：fields 必须是非空的字符串列表")
        fields = []
    else:
        if len(set(fields)) != len(fields):
            problems.append(f"{where}：fields 里存在重复字段")
        missing = [f for f in expected_fields if f not in fields]
        extra = [f for f in fields if f not in expected_fields]
        if missing or extra:
            detail = "，".join(
                ([f"缺少 {missing}"] if missing else [])
                + ([f"多出 {extra}"] if extra else [])
            )
            problems.append(f"{where}：字段与代码登记的对不上（{detail}）")
        elif list(fields) != list(expected_fields):
            problems.append(f"{where}：字段顺序与代码登记的不一致，应为 {expected_fields}")

    records = spec.get("records")
    if not isinstance(records, list):
        problems.append(f"{where}：records 必须是列表（没有初始记录就写 records: []）")
        records = []

    seen_ids: set[int] = set()
    for index, record in enumerate(records):
        _validate_record(where, index, record, fields, seen_ids, problems)

    return ModuleSeed(
        label=str(label),
        fields=list(fields),
        records=[r for r in records if isinstance(r, dict)],
    )


def _validate_record(
    where: str,
    index: int,
    record: Any,
    fields: list[str],
    seen_ids: set[int],
    problems: list[str],
) -> None:
    at = f"{where} 第 {index + 1} 条记录"
    if not isinstance(record, dict):
        problems.append(f"{at}：必须是 id/status/pending/abnormal/values 键值结构")
        return

    for key in sorted(set(record) - RECORD_KEYS):
        problems.append(f"{at}：有不认识的键「{key}」")

    rid = record.get("id")
    if isinstance(rid, bool) or not isinstance(rid, int):
        problems.append(f"{at}：id 必须是整数")
    elif rid in seen_ids:
        problems.append(f"{at}：id {rid} 与本表前面的记录重复")
    else:
        seen_ids.add(rid)
        at = f"{where} 记录 id={rid}"

    status = record.get("status")
    if not isinstance(status, str) or not status.strip():
        problems.append(f"{at}：status 必须是非空字符串")
    for flag in ("pending", "abnormal"):
        if not isinstance(record.get(flag), bool):
            problems.append(f"{at}：{flag} 必须是布尔值 true/false")

    values = record.get("values")
    if not isinstance(values, dict):
        problems.append(f"{at}：values 必须是键值结构，键为 fields 里声明的字段")
        return
    missing = [f for f in fields if f not in values]
    extra = [f for f in values if f not in fields]
    if missing:
        problems.append(f"{at}：values 缺少字段 {missing}")
    if extra:
        problems.append(f"{at}：values 多出未声明的字段 {extra}")
    for key, value in values.items():
        if not isinstance(value, ALLOWED_VALUE_TYPES):
            problems.append(
                f"{at}：字段「{key}」的值类型不支持（{type(value).__name__}），"
                "日期等请写成带引号的字符串"
            )


def load_manifest(path: Path) -> dict[str, ModuleSeed]:
    """读取并校验清单文件；文件读不到、YAML 不合法、结构对不上都会抛 ManifestError。"""
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise ManifestError([f"清单文件读不到：{path}（{exc.strerror or exc}）"]) from exc
    try:
        doc = yaml.safe_load(text)
    except yaml.YAMLError as exc:
        raise ManifestError([f"清单不是合法的 YAML：{exc}"]) from exc
    return validate_document(doc)


def build_tables(
    seeds: dict[str, ModuleSeed], scale: int = 1
) -> dict[str, list[dict[str, Any]]]:
    """把清单记录展开成内存表。

    scale > 1 时按批复制：id 按批次偏移，第一个字段（编号）追加批次后缀，
    其余字段原样复制——环境之间只允许数据量不同，结构完全一致。
    """
    tables: dict[str, list[dict[str, Any]]] = {}
    for name, seed in seeds.items():
        rows: list[dict[str, Any]] = []
        for batch in range(max(1, scale)):
            for record in seed.records:
                row: dict[str, Any] = {
                    "id": record["id"] + batch * ID_STRIDE,
                    "status": record["status"],
                    "pending": record["pending"],
                    "abnormal": record["abnormal"],
                }
                values = dict(record["values"])
                if batch:
                    first_field = seed.fields[0]
                    values[first_field] = f"{values[first_field]}-S{batch + 1:02d}"
                row.update(values)
                rows.append(row)
        tables[name] = rows
    return tables


def write_snapshot(tables: dict[str, list[dict[str, Any]]], path: Path) -> None:
    """把成功装载的结果写成本地快照，供清单损坏时回退。临时文件 + 原子替换。"""
    payload = {
        "version": MANIFEST_VERSION,
        "saved_at": datetime.now(timezone.utc).isoformat(),
        "tables": tables,
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(dir=path.parent, prefix=path.name, suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(payload, fh, ensure_ascii=False)
        os.replace(tmp_name, path)
    except BaseException:
        try:
            os.unlink(tmp_name)
        except OSError:
            pass
        raise


def read_snapshot(path: Path) -> dict[str, list[dict[str, Any]]] | None:
    """读取上一次成功装载的快照；文件不存在或内容损坏时返回 None。"""
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(payload, dict) or payload.get("version") != MANIFEST_VERSION:
        return None
    tables = payload.get("tables")
    if not isinstance(tables, dict) or not tables:
        return None
    for rows in tables.values():
        if not isinstance(rows, list) or any(not isinstance(row, dict) for row in rows):
            return None
    return tables


def _load_from_manifest(settings: Settings) -> tuple[dict[str, list[dict[str, Any]]], LoadReport]:
    seeds = load_manifest(Path(settings.seed_manifest))
    tables = build_tables(seeds, settings.seed_scale)
    report = LoadReport(
        source="manifest",
        path=str(settings.seed_manifest),
        scale=settings.seed_scale,
        modules=len(tables),
        records=sum(len(rows) for rows in tables.values()),
    )
    return tables, report


def _commit(store: Store, settings: Settings, tables: dict[str, list[dict[str, Any]]], report: LoadReport) -> None:
    store.load(tables)
    store.seed_report = report
    try:
        write_snapshot(tables, Path(settings.seed_snapshot))
    except OSError:
        logger.warning("快照写入失败：%s（不影响本次装载）", settings.seed_snapshot, exc_info=True)


def bootstrap_store(store: Store, settings: Settings) -> LoadReport:
    """进程启动时装载示例数据。

    清单优先；清单损坏时回退到上一次成功装载的快照并以 degraded 状态运行；
    连快照都没有时抛 ManifestError，让进程在启动阶段直接失败。
    """
    try:
        tables, report = _load_from_manifest(settings)
    except ManifestError as exc:
        snapshot = read_snapshot(Path(settings.seed_snapshot))
        if snapshot is None:
            logger.error("清单装载失败，且没有可回退的快照，启动中止。\n%s", exc)
            raise
        report = LoadReport(
            source="snapshot",
            path=str(settings.seed_snapshot),
            scale=1,
            modules=len(snapshot),
            records=sum(len(rows) for rows in snapshot.values()),
            degraded=True,
            problems=exc.problems,
        )
        store.load(snapshot)
        store.seed_report = report
        logger.error(
            "清单装载失败，已回退到上一次成功装载的数据（%s）。"
            "修好清单后重启，或调用 POST /api/admin/seed/reload 重试。\n%s",
            settings.seed_snapshot,
            exc,
        )
        return report
    _commit(store, settings, tables, report)
    logger.info(
        "示例数据装载完成：%d 个模块、%d 条记录（来源 %s，scale=%d）",
        report.modules,
        report.records,
        report.path,
        report.scale,
    )
    return report


def reload_store(store: Store, settings: Settings) -> LoadReport:
    """运行中重试装载：校验失败抛 ManifestError，内存里的数据保持不动。"""
    tables, report = _load_from_manifest(settings)
    # 校验不过会在这里抛出，不会触碰 store
    _commit(store, settings, tables, report)
    logger.info(
        "示例数据已重新装载：%d 个模块、%d 条记录（scale=%d）",
        report.modules,
        report.records,
        report.scale,
    )
    return report


def main(argv: list[str] | None = None) -> int:
    """命令行校验：`python -m app.seed_loader [清单路径]`，构建期与手工检查都用它。"""
    parser = argparse.ArgumentParser(description="校验示例数据清单")
    parser.add_argument("path", nargs="?", default=None, help="清单路径，默认取配置里的 seed_manifest")
    args = parser.parse_args(argv)

    from app.config import settings

    path = Path(args.path or settings.seed_manifest)
    try:
        seeds = load_manifest(path)
    except ManifestError as exc:
        print(exc, file=sys.stderr)
        return 1
    total = 0
    for name in sorted(seeds):
        seed = seeds[name]
        total += len(seed.records)
        print(f"{name}\t{seed.label}\t字段 {len(seed.fields)} 个\t记录 {len(seed.records)} 条")
    print(f"合计 {len(seeds)} 个模块、{total} 条初始记录，清单校验通过：{path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
