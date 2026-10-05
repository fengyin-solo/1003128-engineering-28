"""运行配置：端口、跨域、运行环境与初始化数据清单路径。

所有路径都相对后端根目录（本文件的上两级）解析，保证本地、构建镜像和部署时
读到的都是同一份 data/seed_manifest.json；环境差异只通过 SEED_RECORDS_DIR
覆盖记录数据，结构始终由清单本身约束。
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

BACKEND_ROOT = Path(__file__).resolve().parent.parent


def _env_bool(name: str, default: bool = False) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _env_path(name: str, default: Path) -> Path:
    raw = os.environ.get(name)
    return Path(raw).expanduser() if raw else default


@dataclass(frozen=True)
class Settings:
    app_name: str = field(default_factory=lambda: os.environ.get("APP_NAME", "矿山安全监测管理平台"))
    env: str = field(default_factory=lambda: os.environ.get("APP_ENV", "local"))
    port: int = field(default_factory=lambda: int(os.environ.get("APP_PORT", "8000")))
    allowed_origins: list[str] = field(
        default_factory=lambda: [
            "http://127.0.0.1:5173",
            "http://localhost:5173",
        ]
    )
    page_size_default: int = 20
    page_size_max: int = 200

    seed_manifest_path: Path = field(
        default_factory=lambda: _env_path("SEED_MANIFEST", BACKEND_ROOT / "data" / "seed_manifest.json")
    )
    # 设为非空目录时，目录里的 <模块名>.json 会整体替换清单内该表的初始记录；
    # 结构（字段/状态）仍按清单与代码注册表校验，环境间只允许数据量不同。
    seed_records_dir: Path | None = field(
        default_factory=lambda: (
            _env_path("SEED_RECORDS_DIR", BACKEND_ROOT / "data" / "records")
            if os.environ.get("SEED_RECORDS_DIR")
            else None
        )
    )
    seed_snapshot_path: Path = field(
        default_factory=lambda: _env_path("SEED_SNAPSHOT", BACKEND_ROOT / "data" / "runtime" / "seed_last_good.json")
    )
    # 清单损坏时是否退回上一次成功装载的快照；默认关闭：坏清单必须在启动阶段失败。
    seed_fallback_snapshot: bool = field(default_factory=lambda: _env_bool("SEED_FALLBACK_SNAPSHOT"))


settings = Settings()
