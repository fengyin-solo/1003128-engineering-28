"""运行配置：端口、跨域、运行环境与示例数据清单。"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

BACKEND_ROOT = Path(__file__).resolve().parent.parent


def _seed_scale() -> int:
    """数据量开关：各环境只允许条数不同，结构由同一份清单保证一致。"""
    raw = os.environ.get("SEED_SCALE", "1")
    try:
        scale = int(raw)
    except ValueError:
        raise ValueError(f"SEED_SCALE 必须是正整数，当前是 {raw!r}") from None
    if scale < 1:
        raise ValueError(f"SEED_SCALE 必须 >= 1，当前是 {scale}")
    return scale


@dataclass(frozen=True)
class Settings:
    app_name: str = "矿山安全监测管理平台"
    env: str = field(default_factory=lambda: os.environ.get("APP_ENV", "local"))
    port: int = 8000
    allowed_origins: list[str] = field(
        default_factory=lambda: [
            "http://127.0.0.1:5173",
            "http://localhost:5173",
        ]
    )
    page_size_default: int = 20
    page_size_max: int = 200
    # 示例数据清单：本地开发、构建镜像、部署都读这一份
    seed_manifest: str = field(
        default_factory=lambda: os.environ.get(
            "SEED_MANIFEST", str(BACKEND_ROOT / "seed" / "manifest.yaml")
        )
    )
    # 上一次成功装载的快照：清单损坏时回退到它（运行期本地文件，不入库）
    seed_snapshot: str = field(
        default_factory=lambda: os.environ.get(
            "SEED_SNAPSHOT", str(BACKEND_ROOT / "var" / "last-good.seed")
        )
    )
    seed_scale: int = field(default_factory=_seed_scale)


settings = Settings()
