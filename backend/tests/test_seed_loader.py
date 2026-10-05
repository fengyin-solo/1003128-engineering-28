"""示例数据清单的回归测试：结构校验、原子替换、快照回退与概览统计一致性。

运行：cd backend && .venv/bin/python -m pytest tests/ -q
"""
from __future__ import annotations

import dataclasses
from pathlib import Path

import pytest
import yaml

from app.config import settings
from app.seed_loader import (
    ManifestError,
    bootstrap_store,
    build_tables,
    load_manifest,
    read_snapshot,
    reload_store,
    validate_document,
    write_snapshot,
)
from app.store import Store

MANIFEST_PATH = Path(settings.seed_manifest)


def load_doc() -> dict:
    return yaml.safe_load(MANIFEST_PATH.read_text(encoding="utf-8"))


def make_settings(tmp_path: Path, **overrides):
    defaults = {
        "seed_snapshot": str(tmp_path / "last-good.seed"),
        "seed_scale": 1,
    }
    defaults.update(overrides)
    return dataclasses.replace(settings, **defaults)


def test_manifest_is_valid_and_complete() -> None:
    seeds = load_manifest(MANIFEST_PATH)
    assert len(seeds) == 20
    tables = build_tables(seeds, scale=1)
    assert sum(len(rows) for rows in tables.values()) == 60
    for name, seed in seeds.items():
        assert len(seed.records) == 3
        for row in tables[name]:
            assert set(row) == {"id", "status", "pending", "abnormal"} | set(seed.fields)


def test_overview_counts_match_manifest(tmp_path: Path) -> None:
    """运营概览的统计必须直接读到清单装载出来的记录，两边同一个数。"""
    store = Store()
    report = bootstrap_store(store, make_settings(tmp_path))
    overview = store.overview()
    cards = {card["label"]: card["value"] for card in overview["cards"]}
    assert cards["业务模块"] == 20
    assert cards["今日新增"] == report.records == 60
    for module in overview["modules"]:
        assert module["created"] == 3
        assert module["pending"] == 2
        assert module["abnormal"] == 1


def test_missing_module_fails_and_names_table() -> None:
    doc = load_doc()
    del doc["modules"]["gas"]
    with pytest.raises(ManifestError) as err:
        validate_document(doc)
    assert "gas" in str(err.value)


def test_field_mismatch_fails_and_names_table_and_field() -> None:
    doc = load_doc()
    fields = doc["modules"]["gas"]["fields"]
    fields[fields.index("瓦斯浓度")] = "瓦斯浓度v2"
    with pytest.raises(ManifestError) as err:
        validate_document(doc)
    message = str(err.value)
    assert "gas" in message and "瓦斯监测" in message
    assert "瓦斯浓度" in message and "瓦斯浓度v2" in message


def test_record_missing_field_fails_and_names_table() -> None:
    doc = load_doc()
    del doc["modules"]["belt"]["records"][0]["values"]["皮带编号"]
    with pytest.raises(ManifestError) as err:
        validate_document(doc)
    message = str(err.value)
    assert "belt" in message and "皮带编号" in message


def test_failed_reload_keeps_previous_data(tmp_path: Path) -> None:
    store = Store()
    bootstrap_store(store, make_settings(tmp_path))
    before = [dict(row) for row in store.rows("gas")]

    broken = load_doc()
    del broken["modules"]["gas"]
    broken_path = tmp_path / "broken.yaml"
    broken_path.write_text(yaml.dump(broken, allow_unicode=True), encoding="utf-8")

    with pytest.raises(ManifestError):
        reload_store(store, make_settings(tmp_path, seed_manifest=str(broken_path)))
    assert store.rows("gas") == before
    assert sum(len(store.rows(m)) for m in store.module_names()) == 60


def test_bootstrap_falls_back_to_snapshot(tmp_path: Path) -> None:
    good_settings = make_settings(tmp_path)
    first = Store()
    bootstrap_store(first, good_settings)
    assert Path(good_settings.seed_snapshot).exists()

    broken = load_doc()
    broken["modules"]["roof"]["fields"].remove("离层量")
    broken_path = tmp_path / "broken.yaml"
    broken_path.write_text(yaml.dump(broken, allow_unicode=True), encoding="utf-8")

    second = Store()
    report = bootstrap_store(
        second, make_settings(tmp_path, seed_manifest=str(broken_path))
    )
    assert report.degraded is True
    assert report.source == "snapshot"
    assert any("roof" in p for p in report.problems)
    assert second.rows("roof") == first.rows("roof")
    assert sum(len(second.rows(m)) for m in second.module_names()) == 60


def test_bootstrap_without_snapshot_fails(tmp_path: Path) -> None:
    broken = load_doc()
    del broken["modules"]["power"]
    broken_path = tmp_path / "broken.yaml"
    broken_path.write_text(yaml.dump(broken, allow_unicode=True), encoding="utf-8")

    with pytest.raises(ManifestError) as err:
        bootstrap_store(Store(), make_settings(tmp_path, seed_manifest=str(broken_path)))
    assert "power" in str(err.value)


def test_scale_multiplies_records_with_unique_ids(tmp_path: Path) -> None:
    store = Store()
    report = bootstrap_store(store, make_settings(tmp_path, seed_scale=3))
    assert report.records == 180
    rows = store.rows("gas")
    assert len(rows) == 9
    ids = [row["id"] for row in rows]
    assert len(set(ids)) == len(ids)
    codes = [row["测点编号"] for row in rows]
    assert codes[0] == "GAS-0001"
    assert codes[3] == "GAS-0001-S02"
    assert codes[6] == "GAS-0001-S03"
    cards = {card["label"]: card["value"] for card in store.overview()["cards"]}
    assert cards["今日新增"] == 180


def test_snapshot_roundtrip(tmp_path: Path) -> None:
    seeds = load_manifest(MANIFEST_PATH)
    tables = build_tables(seeds, scale=1)
    snapshot = tmp_path / "var" / "last-good.seed"
    write_snapshot(tables, snapshot)
    assert read_snapshot(snapshot) == tables
    assert read_snapshot(tmp_path / "missing.seed") is None
    snapshot.write_text("not json", encoding="utf-8")
    assert read_snapshot(snapshot) is None
