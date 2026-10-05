"""初始化数据清单的装载与校验。

示例数据不再写死在代码里，而是由 ``data/seed_manifest.json`` 这份清单按模块（表）
声明字段与初始记录，进程启动时统一装载。表结构以代码侧 ``routers`` / ``services``
的常量为唯一注册表，清单必须与注册表逐一对齐：

* 清单缺模块、多出未知模块、字段对不上、记录键缺失/多余、id 重复、status 非法等，
  全部在启动阶段报错并点名是哪一张表，**绝不静默跳过**；
* 装载先在暂存区构建出完整数据，全部校验通过后才整体换入内存仓库（事务式），
  失败时现役数据（上一次成功装载的数据）原样保留，修好清单重启或调用
  ``POST /api/seed/reload`` 即可重试；
* 每次装载成功都会把结果原子写入 last-good 快照（``data/runtime/seed_last_good.json``），
  显式设置 ``SEED_FALLBACK_SNAPSHOT=1`` 时，清单坏了可用上一份好数据继续起服务；
* 本地开发、构建和部署读同一份清单；环境之间只允许通过 ``SEED_RECORDS_DIR`` 覆盖
  某些表的记录（数据量不同），覆盖记录走同样的结构校验，结构必须一致。

命令行：

* ``python -m app.seed_loader --check``：只校验清单（构建镜像、run.sh、CI 都调它）；
* ``python -m app.seed_loader --snapshot-info``：校验并查看上一次成功装载的快照。
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import tempfile
from dataclasses import dataclass
from importlib import import_module
from pathlib import Path
from typing import Any

from app.config import settings
from app.routers import ROUTERS

logger = logging.getLogger("app.seed")

MANIFEST_VERSION = 1
META_KEYS = ("id", "status", "pending", "abnormal")


class ManifestError(Exception):
    """清单校验失败：errors 里每一条都点名出问题的表与原因。"""

    def __init__(self, errors: list[str]) -> None:
        self.errors = errors
        head = f"初始化数据清单校验失败（共 {len(errors)} 处问题，启动已中止）："
        super().__init__(head + "\n  - " + "\n  - ".join(errors))


@dataclass(frozen=True)
class TableSpec:
    """一张表在代码侧的结构注册信息（字段、必填字段、状态序列）。"""

    name: str
    label: str
    fields: list[str]
    required_fields: list[str]
    statuses: list[str]

    def where(self) -> str:
        return f"表「{self.name}」（{self.label}）"


def discover_registry() -> dict[str, TableSpec]:
    """从 routers/services 汇总出 20 张表的结构注册表。

    router 的 LIST_FIELDS/STATUSES 与 service 的 REQUIRED_FIELDS/STATUS_ORDER 本就是
    每张表的事实结构；这里顺手交叉校验，代码侧改了字段却没对齐，启动同样直接失败。
    """
    specs: dict[str, TableSpec] = {}
    for router_module in ROUTERS:
        api_router = router_module.router
        name = api_router.prefix.split("/")[-1]
        rt = import_module(f"app.routers.{name}")
        sv = import_module(f"app.services.{name}")
        label = api_router.tags[0] if api_router.tags else name
        fields = list(rt.LIST_FIELDS)
        statuses = list(rt.STATUSES)
        required_fields = list(sv.REQUIRED_FIELDS)

        errors: list[str] = []
        if getattr(sv, "MODULE", None) != name:
            errors.append(f"service 的 MODULE={getattr(sv, 'MODULE', None)!r} 与路由表名 {name!r} 不一致")
        if not fields or any(not isinstance(f, str) or not f.strip() for f in fields):
            errors.append("LIST_FIELDS 不能为空或含空白字段名")
        if len(fields) != len(set(fields)):
            errors.append(f"LIST_FIELDS 存在重复字段：{fields}")
        missing = [f for f in required_fields if f not in fields]
        if missing:
            errors.append(f"REQUIRED_FIELDS 有字段不在 LIST_FIELDS 中：{missing}")
        if list(sv.STATUS_ORDER) != statuses:
            errors.append(f"router.STATUSES {statuses} 与 service.STATUS_ORDER {list(sv.STATUS_ORDER)} 不一致")
        if errors:
            raise ManifestError([f"表「{name}」（{label}）代码侧结构注册异常：{e}" for e in errors])

        specs[name] = TableSpec(name=name, label=label, fields=fields,
                                required_fields=required_fields, statuses=statuses)
    return specs


def _validate_record(spec: TableSpec, record: Any, index: int, errors: list[str]) -> None:
    """校验单条记录；问题累积进 errors，不抛异常。"""
    where = f"{spec.where()}第 {index + 1} 条记录"
    if not isinstance(record, dict):
        errors.append(f"{where}：必须是对象，实际是 {type(record).__name__}")
        return

    keys = set(record)
    expected = set(META_KEYS) | set(spec.fields)
    for key in META_KEYS:
        if key not in record:
            errors.append(f"{where}：缺少元字段 {key!r}")
    for field in spec.fields:
        if field not in record:
            errors.append(f"{where}：缺少业务字段 {field!r}")
    extra = keys - expected
    if extra:
        errors.append(f"{where}：存在未声明字段 {sorted(extra)}，请先在清单与代码中同步字段")

    value = record.get("id")
    if type(value) is not int or value < 1:
        errors.append(f"{where}：id 必须是正整数，实际为 {value!r}")

    status = record.get("status")
    if status not in spec.statuses:
        errors.append(f"{where}：status {status!r} 不在允许状态 {spec.statuses} 中")

    for flag in ("pending", "abnormal"):
        if type(record.get(flag)) is not bool:
            errors.append(f"{where}：{flag} 必须是布尔值 true/false，实际为 {record.get(flag)!r}")

    for field in spec.fields:
        if field not in record:
            continue
        val = record[field]
        if field in spec.required_fields:
            if type(val) is not str or not val.strip():
                errors.append(f"{where}：必填字段 {field!r} 必须是非空字符串，实际为 {val!r}")
        elif val is not None and not isinstance(val, (str, int, float)):
            errors.append(f"{where}：字段 {field!r} 只允许字符串/数字/null，实际是 {type(val).__name__}")


def _validate_table(spec: TableSpec, fields: Any, records: Any, errors: list[str]) -> None:
    """校验一张表的字段声明与全部记录。"""
    if not isinstance(fields, list) or any(not isinstance(f, str) or not f.strip() for f in fields):
        errors.append(f"{spec.where()}：fields 必须是非空字符串数组")
    else:
        missing = [f for f in spec.fields if f not in fields]
        extra = [f for f in fields if f not in spec.fields]
        if missing:
            errors.append(f"{spec.where()}：清单缺少字段 {missing}，代码侧实际字段为 {spec.fields}")
        if extra:
            errors.append(f"{spec.where()}：清单多出未注册字段 {extra}，代码侧实际字段为 {spec.fields}")
        if not missing and not extra and fields != spec.fields:
            errors.append(f"{spec.where()}：字段顺序与代码不一致，清单 {fields}，要求 {spec.fields}")

    if not isinstance(records, list):
        errors.append(f"{spec.where()}：records 必须是数组（允许空数组，但字段必须保留）")
        return

    seen_ids: set[int] = set()
    for index, record in enumerate(records):
        _validate_record(spec, record, index, errors)
        if isinstance(record, dict) and isinstance(record.get("id"), int) and not isinstance(record.get("id"), bool):
            rid = record["id"]
            if rid in seen_ids:
                errors.append(f"{spec.where()}第 {index + 1} 条记录：id {rid} 重复")
            seen_ids.add(rid)


def _read_json(path: Path, errors: list[str], what: str) -> Any:
    try:
        raw = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        errors.append(f"{what}不存在：{path}")
        return None
    except OSError as exc:
        errors.append(f"{what}无法读取 {path}：{exc}")
        return None
    try:
        return json.loads(raw)
    except json.JSONDecodeError as exc:
        errors.append(f"{what}不是合法 JSON（{path} 第 {exc.lineno} 行第 {exc.colno} 列）：{exc.msg}")
        return None


def _validate_manifest_document(payload: Any, registry: dict[str, TableSpec]) -> dict[str, list[dict[str, Any]]]:
    """对清单文档做全量结构校验，返回 {模块名: 原始记录列表}（尚未合并覆盖目录）。"""
    errors: list[str] = []
    if not isinstance(payload, dict):
        raise ManifestError([f"清单顶层必须是对象，实际是 {type(payload).__name__}"])

    version = payload.get("version")
    if version != MANIFEST_VERSION:
        errors.append(f"清单 version 必须是 {MANIFEST_VERSION}，实际为 {version!r}")

    modules = payload.get("modules")
    if not isinstance(modules, list):
        raise ManifestError(errors + ["清单顶层 modules 必须是数组"])

    declared: dict[str, dict[str, Any]] = {}
    for index, module in enumerate(modules):
        if not isinstance(module, dict):
            errors.append(f"modules 第 {index + 1} 项必须是对象，实际是 {type(module).__name__}")
            continue
        name = module.get("name")
        if not isinstance(name, str) or not name.strip():
            errors.append(f"modules 第 {index + 1} 项缺少 name（字符串）")
            continue
        if name in declared:
            errors.append(f"模块「{name}」在清单里重复声明")
        spec = registry.get(name)
        if spec is None:
            errors.append(f"清单声明了未注册模块「{name}」：代码侧没有对应的 router/service，请先加模块或改正表名")
            continue
        label = module.get("label")
        if label is not None and label != spec.label:
            errors.append(f"{spec.where()}：label {label!r} 与代码侧 {spec.label!r} 不一致")
        declared[name] = module

    missing = [f"{n}（{s.label}）" for n, s in registry.items() if n not in declared]
    if missing:
        errors.append(f"清单缺少 {len(missing)} 张表：{missing}；新增/删除模块必须同步清单，不许静默跳过")

    # 即使清单缺表/有多出的表，已声明且能对上注册表的表也要继续逐表校验，
    # 一次把所有问题暴露完，避免修一处重启再报下一处。
    raw_records: dict[str, list[dict[str, Any]]] = {}
    for name, module in declared.items():
        if name not in registry:
            continue
        spec = registry[name]
        _validate_table(spec, module.get("fields"), module.get("records"), errors)
        raw_records[name] = module.get("records") if isinstance(module.get("records"), list) else []
    if errors:
        raise ManifestError(errors)
    return raw_records


def _apply_records_dir(raw_records: dict[str, list[dict[str, Any]]],
                       records_dir: Path, registry: dict[str, TableSpec]) -> dict[str, list[dict[str, Any]]]:
    """用环境覆盖目录里的 <模块>.json 替换对应表的记录；只换数据，不换结构。"""
    errors: list[str] = []
    if not records_dir.exists():
        raise ManifestError([f"SEED_RECORDS_DIR 指向的覆盖目录不存在：{records_dir}"])
    if not records_dir.is_dir():
        raise ManifestError([f"SEED_RECORDS_DIR 必须是目录：{records_dir}"])

    merged = dict(raw_records)
    for path in sorted(records_dir.glob("*.json")):
        name = path.stem
        spec = registry.get(name)
        if spec is None:
            errors.append(f"覆盖目录里有未注册文件 {path.name}：代码侧没有表「{name}」，环境数据不允许出现清单外结构")
            continue
        payload = _read_json(path, errors, "环境覆盖记录")
        if payload is None:
            continue
        if not isinstance(payload, list):
            errors.append(f"{spec.where()}覆盖记录 {path} 顶层必须是记录数组")
            continue
        _validate_table(spec, spec.fields, payload, errors)
        merged[name] = payload

    unused = sorted(p.name for p in records_dir.iterdir() if p.is_file() and p.suffix != ".json")
    for name in unused:
        errors.append(f"覆盖目录 {records_dir} 里有非 JSON 文件 {name}，只允许放 <模块名>.json")
    if errors:
        raise ManifestError(errors)
    return merged


def _materialize(registry: dict[str, TableSpec],
                 raw_records: dict[str, list[dict[str, Any]]]) -> dict[str, list[dict[str, Any]]]:
    """把已校验的原始记录按声明顺序物化成内存表（深拷贝，避免清单对象被运行时改写）。"""
    tables: dict[str, list[dict[str, Any]]] = {}
    for name, spec in registry.items():
        rows = []
        for record in raw_records.get(name, []):
            row = {"id": record["id"], "status": record["status"],
                   "pending": record["pending"], "abnormal": record["abnormal"]}
            for field in spec.fields:
                row[field] = record[field]
            rows.append(row)
        tables[name] = rows
    return tables


def load_manifest_tables(
    *,
    manifest_path: Path | str | None = None,
    records_dir: Path | str | None = None,
    registry: dict[str, TableSpec] | None = None,
) -> tuple[dict[str, list[dict[str, Any]]], dict[str, Any]]:
    """读取并校验清单，返回 (表数据, 装载元信息)。

    任何校验问题都会抛 ManifestError；只有全部模块全部记录都通过时才返回数据，
    调用方因此可以安全地把返回值整体换入内存仓库，不存在“装了一半”的状态。
    """
    manifest_path = Path(manifest_path) if manifest_path else settings.seed_manifest_path
    records_dir = Path(records_dir) if records_dir is not None else settings.seed_records_dir
    registry = registry or discover_registry()

    read_errors: list[str] = []
    payload = _read_json(manifest_path, read_errors, "初始化数据清单")
    if payload is None:
        raise ManifestError(read_errors or [f"初始化数据清单无法读取：{manifest_path}"])
    raw_records = _validate_manifest_document(payload, registry)
    if records_dir is not None:
        raw_records = _apply_records_dir(raw_records, records_dir, registry)

    tables = _materialize(registry, raw_records)
    meta = {
        "source": "manifest",
        "manifest_path": str(manifest_path),
        "records_dir": str(records_dir) if records_dir else None,
        "counts": {name: len(rows) for name, rows in tables.items()},
        "total": sum(len(rows) for rows in tables.values()),
    }
    return tables, meta


def save_snapshot(tables: dict[str, list[dict[str, Any]]], meta: dict[str, Any],
                  snapshot_path: Path | str | None = None) -> Path:
    """把一次成功装载的结果原子写入 last-good 快照（先写临时文件再 rename）。"""
    snapshot_path = Path(snapshot_path) if snapshot_path else settings.seed_snapshot_path
    snapshot_path.parent.mkdir(parents=True, exist_ok=True)
    payload = {"version": MANIFEST_VERSION, "tables": tables, "meta": meta}
    fd, tmp_name = tempfile.mkstemp(prefix=snapshot_path.name, suffix=".tmp", dir=snapshot_path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(payload, fh, ensure_ascii=False, indent=2)
            fh.write("\n")
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp_name, snapshot_path)
    except BaseException:
        Path(tmp_name).unlink(missing_ok=True)
        raise
    return snapshot_path


def load_snapshot(
    *,
    snapshot_path: Path | str | None = None,
    registry: dict[str, TableSpec] | None = None,
) -> tuple[dict[str, list[dict[str, Any]]], dict[str, Any]]:
    """读取并校验 last-good 快照；结构对不上当前代码同样直接失败。"""
    snapshot_path = Path(snapshot_path) if snapshot_path else settings.seed_snapshot_path
    registry = registry or discover_registry()
    errors: list[str] = []
    payload = _read_json(snapshot_path, errors, "上一次成功装载的快照")
    if payload is None:
        raise ManifestError(errors)
    if not isinstance(payload, dict) or payload.get("version") != MANIFEST_VERSION:
        raise ManifestError([f"快照 {snapshot_path} 版本不受支持（需要 version={MANIFEST_VERSION}）"])
    raw_tables = payload.get("tables")
    if not isinstance(raw_tables, dict):
        raise ManifestError([f"快照 {snapshot_path} 的 tables 必须是对象"])

    unknown = [name for name in raw_tables if name not in registry]
    missing = [name for name in registry if name not in raw_tables]
    if unknown:
        errors.append(f"快照里有清单外的表 {unknown}")
    if missing:
        errors.append(f"快照缺少表 {missing}")
    for name, spec in registry.items():
        if name in raw_tables:
            _validate_table(spec, spec.fields, raw_tables[name], errors)
    if errors:
        raise ManifestError(errors)

    raw_records = {name: raw_tables[name] for name in registry}
    tables = _materialize(registry, raw_records)
    meta = dict(payload.get("meta") or {})
    meta["source"] = "snapshot"
    return tables, meta


def bootstrap(store: Any) -> str:
    """启动期装载入口：校验通过才整体换入 store 并刷新快照。

    默认清单坏了就抛 ManifestError（由启动流程转为非零退出）；显式打开
    SEED_FALLBACK_SNAPSHOT 时，退回上一份校验通过的快照数据继续启动。
    返回最终数据来源（manifest / snapshot）。
    """
    registry = discover_registry()
    try:
        tables, meta = load_manifest_tables(registry=registry)
        source = "manifest"
    except ManifestError:
        if not settings.seed_fallback_snapshot:
            raise
        logger.critical("初始化清单装载失败，SEED_FALLBACK_SNAPSHOT 已开启，尝试使用上一次成功装载的快照")
        tables, meta = load_snapshot(registry=registry)
        source = "snapshot"

    store.replace_tables(tables)
    store.loaded_from = source
    path = save_snapshot(tables, meta)
    logger.info(
        "初始化数据装载完成（来源=%s，%d 张表，%d 条记录，快照=%s）",
        source, len(tables), meta["total"], path,
    )
    return source


def _cli(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="初始化数据清单校验工具")
    parser.add_argument("--check", action="store_true", help="校验清单（含 SEED_RECORDS_DIR 覆盖记录）")
    parser.add_argument("--snapshot-info", action="store_true", help="校验并查看上一次成功装载的快照")
    args = parser.parse_args(argv)

    logging.basicConfig(level=logging.INFO, format="%(message)s")
    registry = discover_registry()
    try:
        if args.snapshot_info:
            tables, meta = load_snapshot(registry=registry)
            print(f"快照校验通过：{len(tables)} 张表，{sum(len(r) for r in tables.values())} 条记录")
            print(f"来源信息：{meta.get('manifest_path')}（覆盖目录：{meta.get('records_dir')}）")
        else:
            tables, meta = load_manifest_tables(registry=registry)
            print(f"清单校验通过：{len(tables)} 张表，{meta['total']} 条记录 -> {meta['manifest_path']}")
            if meta["records_dir"]:
                print(f"已合并环境覆盖记录目录：{meta['records_dir']}")
            for name in registry:
                print(f"  {name:15} {registry[name].label:5} {meta['counts'][name]:4d} 条")
    except ManifestError as exc:
        print(str(exc), flush=True)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(_cli())
