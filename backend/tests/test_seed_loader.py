"""初始化清单装载器与运营概览同源的回归测试。

只用标准库：``python -m unittest discover -s tests``。
"""
from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

BACKEND_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BACKEND_ROOT))

from app.seed_loader import (  # noqa: E402
    MANIFEST_VERSION,
    ManifestError,
    discover_registry,
    load_manifest_tables,
    save_snapshot,
    load_snapshot,
)
from app.store import Store  # noqa: E402

MANIFEST_PATH = BACKEND_ROOT / "data" / "seed_manifest.json"
REGISTRY = discover_registry()
META_KEYS_LOCAL = ("id", "status", "pending", "abnormal")


def valid_manifest() -> dict:
    return json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))


def write_manifest(tmp: Path, payload: dict, name: str = "seed_manifest.json") -> Path:
    path = tmp / name
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    return path


class ManifestHappyPathTests(unittest.TestCase):
    def test_manifest_matches_code_registry_exactly(self) -> None:
        """随仓清单：20 张表齐全，字段/必填/状态都与代码注册表一致。"""
        tables, meta = load_manifest_tables(manifest_path=MANIFEST_PATH, registry=REGISTRY)
        self.assertEqual(set(tables), set(REGISTRY))
        self.assertEqual(meta["total"], 60)
        for name, spec in REGISTRY.items():
            for row in tables[name]:
                self.assertEqual(set(row), set(META_KEYS_LOCAL) | set(spec.fields))

    def test_built_manifest_is_deep_copy(self) -> None:
        """装载出的表是深拷贝：改运行时数据不会回写清单对象。"""
        tables, _ = load_manifest_tables(manifest_path=MANIFEST_PATH, registry=REGISTRY)
        first = tables["minearea"][0]
        first["矿区名称"] = "被运行时改过"
        again, _ = load_manifest_tables(manifest_path=MANIFEST_PATH, registry=REGISTRY)
        self.assertEqual(again["minearea"][0]["矿区名称"], "矿区台账样例1")


class ManifestValidationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.tmpdir = Path(self.tmp.name)

    def load(self, payload: dict, **kwargs):
        return load_manifest_tables(
            manifest_path=write_manifest(self.tmpdir, payload),
            records_dir=None,
            registry=REGISTRY,
            **kwargs,
        )

    def test_missing_module_names_the_table(self) -> None:
        payload = valid_manifest()
        payload["modules"] = [m for m in payload["modules"] if m["name"] != "rescue"]
        with self.assertRaises(ManifestError) as ctx:
            self.load(payload)
        self.assertTrue(any("rescue" in e and "应急救援" in e for e in ctx.exception.errors))

    def test_unknown_module_rejected(self) -> None:
        payload = valid_manifest()
        payload["modules"].append({"name": "newcoalmine", "label": "新模块", "fields": [], "records": []})
        with self.assertRaises(ManifestError) as ctx:
            self.load(payload)
        self.assertTrue(any("newcoalmine" in e for e in ctx.exception.errors))

    def test_field_mismatch_names_table_and_field(self) -> None:
        payload = valid_manifest()
        for mod in payload["modules"]:
            if mod["name"] == "gas":
                mod["fields"][2] = "瓦斯百分浓度"
        with self.assertRaises(ManifestError) as ctx:
            self.load(payload)
        joined = "\n".join(ctx.exception.errors)
        self.assertIn("gas", joined)
        self.assertIn("瓦斯浓度", joined)
        self.assertIn("瓦斯百分浓度", joined)

    def test_record_missing_key_bad_status_and_duplicate_id(self) -> None:
        payload = valid_manifest()
        for mod in payload["modules"]:
            if mod["name"] == "roof":
                del mod["records"][0]["锚杆受力"]
                mod["records"][0]["status"] = "不存在的状态"
                mod["records"][1]["id"] = mod["records"][0]["id"]
        with self.assertRaises(ManifestError) as ctx:
            self.load(payload)
        joined = "\n".join(ctx.exception.errors)
        self.assertIn("roof", joined)
        self.assertIn("锚杆受力", joined)
        self.assertIn("不存在的状态", joined)
        self.assertIn("id 1 重复", joined)

    def test_extra_record_field_rejected(self) -> None:
        payload = valid_manifest()
        for mod in payload["modules"]:
            if mod["name"] == "belt":
                mod["records"][0]["多余字段"] = "x"
        with self.assertRaises(ManifestError) as ctx:
            self.load(payload)
        self.assertTrue(any("belt" in e and "多余字段" in e for e in ctx.exception.errors))

    def test_required_field_blank_rejected(self) -> None:
        payload = valid_manifest()
        for mod in payload["modules"]:
            if mod["name"] == "minearea":
                mod["records"][0]["矿区编号"] = "  "
        with self.assertRaises(ManifestError) as ctx:
            self.load(payload)
        self.assertTrue(any("必填字段" in e and "矿区编号" in e for e in ctx.exception.errors))

    def test_bad_json_and_missing_file(self) -> None:
        with self.assertRaises(ManifestError):
            load_manifest_tables(manifest_path=self.tmpdir / "nope.json",
                                 records_dir=None, registry=REGISTRY)
        bad = self.tmpdir / "bad.json"
        bad.write_text("{not json", encoding="utf-8")
        with self.assertRaises(ManifestError) as ctx:
            load_manifest_tables(manifest_path=bad, records_dir=None, registry=REGISTRY)
        self.assertTrue(any("不是合法 JSON" in e for e in ctx.exception.errors))

    def test_wrong_version(self) -> None:
        payload = valid_manifest()
        payload["version"] = MANIFEST_VERSION + 1
        with self.assertRaises(ManifestError) as ctx:
            self.load(payload)
        self.assertTrue(any("version" in e for e in ctx.exception.errors))

    def test_bad_value_types(self) -> None:
        payload = valid_manifest()
        for mod in payload["modules"]:
            if mod["name"] == "rescue":
                mod["records"][0]["id"] = 1.5            # id 不接受小数
                mod["records"][0]["pending"] = "yes"     # 布尔字段不接受字符串
                mod["records"][1]["存放地点"] = ["x"]    # 非必填业务字段不接受数组
                mod["records"][2]["装备编号"] = 42       # 必填字段必须是字符串
        with self.assertRaises(ManifestError) as ctx:
            self.load(payload)
        joined = "\n".join(ctx.exception.errors)
        self.assertIn("id 必须是正整数", joined)
        self.assertIn("pending 必须是布尔值", joined)
        self.assertIn("只允许字符串/数字/null", joined)
        self.assertIn("必填字段", joined)

    def test_errors_accumulate_across_tables(self) -> None:
        """多张表同时有问题时要一次报全，不许在第一张表处静默或中断。"""
        payload = valid_manifest()
        payload["modules"] = [m for m in payload["modules"] if m["name"] != "rescue"]
        for mod in payload["modules"]:
            if mod["name"] == "gas":
                mod["fields"][2] = "错误字段"
            if mod["name"] == "roof":
                mod["records"][0]["status"] = "非法"
        with self.assertRaises(ManifestError) as ctx:
            self.load(payload)
        joined = "\n".join(ctx.exception.errors)
        self.assertIn("rescue", joined)
        self.assertIn("gas", joined)
        self.assertIn("roof", joined)

    def test_overlay_replaces_records_volume_only(self) -> None:
        payload = valid_manifest()
        gas = next(m for m in payload["modules"] if m["name"] == "gas")
        records_dir = self.tmpdir / "records"
        records_dir.mkdir()
        row = dict(gas["records"][0])
        rows = []
        for i in range(1, 5):
            r = dict(row)
            r["id"] = i
            r["测点编号"] = f"GAS-T{i:04d}"
            rows.append(r)
        (records_dir / "gas.json").write_text(json.dumps(rows, ensure_ascii=False), encoding="utf-8")
        tables, meta = load_manifest_tables(
            manifest_path=write_manifest(self.tmpdir, payload),
            records_dir=records_dir,
            registry=REGISTRY,
        )
        self.assertEqual(len(tables["gas"]), 4)
        self.assertEqual(tables["gas"][0]["测点编号"], "GAS-T0001")
        self.assertEqual(meta["counts"]["minearea"], 3)

    def test_overlay_structural_error_rejected(self) -> None:
        payload = valid_manifest()
        records_dir = self.tmpdir / "records"
        records_dir.mkdir()
        (records_dir / "gas.json").write_text('[{"id": 1}]', encoding="utf-8")
        with self.assertRaises(ManifestError) as ctx:
            load_manifest_tables(
                manifest_path=write_manifest(self.tmpdir, payload),
                records_dir=records_dir,
                registry=REGISTRY,
            )
        self.assertTrue(any("gas" in e for e in ctx.exception.errors))


class SnapshotTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.tmpdir = Path(self.tmp.name)

    def test_snapshot_roundtrip(self) -> None:
        tables, meta = load_manifest_tables(manifest_path=MANIFEST_PATH, registry=REGISTRY)
        path = save_snapshot(tables, meta, self.tmpdir / "snap.json")
        restored, restored_meta = load_snapshot(snapshot_path=path, registry=REGISTRY)
        self.assertEqual(restored.keys(), tables.keys())
        self.assertEqual(restored["minearea"], tables["minearea"])
        self.assertEqual(restored_meta["source"], "snapshot")

    def test_snapshot_rejects_stale_structure(self) -> None:
        tables, meta = load_manifest_tables(manifest_path=MANIFEST_PATH, registry=REGISTRY)
        # 快照写入后篡改其中字段：结构校验同样拦截，不允许旧结构混入。
        tables["gas"][0]["旧字段"] = tables["gas"][0].pop("瓦斯浓度")
        path = save_snapshot(tables, meta, self.tmpdir / "snap-bad.json")
        with self.assertRaises(ManifestError) as ctx:
            load_snapshot(snapshot_path=path, registry=REGISTRY)
        self.assertTrue(any("gas" in e for e in ctx.exception.errors))


class StoreTransactionTests(unittest.TestCase):
    def test_replace_tables_is_atomic(self) -> None:
        """新数据校验失败时不调用 replace_tables，现役数据原样保留。"""
        tables, _ = load_manifest_tables(manifest_path=MANIFEST_PATH, registry=REGISTRY)
        store = Store()
        store.replace_tables(tables)
        store.loaded_from = "manifest"
        before = store.rows("gas")[0]

        payload = valid_manifest()
        for mod in payload["modules"]:
            if mod["name"] == "gas":
                mod["fields"][2] = "错误字段"
        with tempfile.TemporaryDirectory() as tmp:
            bad_path = Path(tmp) / "bad.json"
            bad_path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
            with self.assertRaises(ManifestError):
                bad_tables, _ = load_manifest_tables(manifest_path=bad_path, registry=REGISTRY)
                store.replace_tables(bad_tables)
        self.assertEqual(store.rows("gas")[0], before)
        self.assertEqual(store.loaded_from, "manifest")

    def test_empty_store_before_bootstrap(self) -> None:
        store = Store()
        self.assertFalse(store.is_loaded())
        self.assertEqual(store.overview()["cards"][0], {"label": "业务模块", "value": 0})


class OverviewConsistencyTests(unittest.TestCase):
    def test_overview_counts_equal_manifest_records(self) -> None:
        """运营概览统计直接读清单装载出的同一份表：两边必须是同一个数。"""
        tables, meta = load_manifest_tables(manifest_path=MANIFEST_PATH, registry=REGISTRY)
        store = Store()
        store.replace_tables(tables)
        overview = store.overview()
        by_name = {row["name"]: row for row in overview["modules"]}
        for name, rows in tables.items():
            self.assertEqual(by_name[name]["created"], len(rows))
            self.assertEqual(by_name[name]["pending"], sum(1 for r in rows if r["pending"]))
            self.assertEqual(by_name[name]["abnormal"], sum(1 for r in rows if r["abnormal"]))
        self.assertEqual(overview["cards"][1]["value"], meta["total"])
        self.assertEqual(overview["cards"][0]["value"], len(tables))


if __name__ == "__main__":
    unittest.main()
