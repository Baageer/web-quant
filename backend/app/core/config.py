"""服务配置。

配置优先级：进程环境变量 > 项目根目录 .env > 默认值。
不引入 python-dotenv，保持依赖最小。
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

from app.core.data_mode import DataMode


def _project_root() -> Path:
    # backend/app/core/config.py -> backend/app/core -> backend/app -> backend -> web-quant
    return Path(__file__).resolve().parents[3]


def _load_env_file(path: Path) -> None:
    if not path.exists():
        return
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key and key not in os.environ:
            os.environ[key] = value


def _env_int(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name, "").strip() or default)
    except ValueError:
        return default


def _env_float(name: str, default: float) -> float:
    try:
        return float(os.environ.get(name, "").strip() or default)
    except ValueError:
        return default


@dataclass(frozen=True)
class Settings:
    root_dir: Path
    data_dir: Path
    samples_dir: Path
    db_path: Path
    frontend_dist: Path
    mode: DataMode
    ts_token: str
    calls_per_min: int
    max_retries: int
    timeout_seconds: int
    log_level: str
    api_host: str
    api_port: int
    radar_min_avg_amount: float

    @property
    def network_enabled(self) -> bool:
        return self.mode.allows_network

    @property
    def token_configured(self) -> bool:
        return bool(self.ts_token)


def build_settings() -> Settings:
    root = _project_root()
    _load_env_file(root / ".env")
    data_dir = Path(os.environ.get("WEB_QUANT_DATA_DIR", "") or (root / "data"))
    if not data_dir.is_absolute():
        data_dir = root / data_dir
    db_path = Path(
        os.environ.get("WEB_QUANT_DB", "") or (data_dir / "meta" / "web_quant.db")
    )
    if not db_path.is_absolute():
        db_path = root / db_path
    return Settings(
        root_dir=root,
        data_dir=data_dir,
        samples_dir=data_dir / "samples",
        db_path=db_path,
        frontend_dist=root / "frontend" / "dist",
        mode=DataMode.parse(os.environ.get("WEB_QUANT_MODE")),
        ts_token=os.environ.get("TS_TOKEN", "").strip(),
        calls_per_min=_env_int("TS_CALLS_PER_MIN", 120),
        max_retries=_env_int("TS_MAX_RETRIES", 5),
        timeout_seconds=_env_int("TS_TIMEOUT", 60),
        log_level=os.environ.get("LOG_LEVEL", "INFO").upper(),
        api_host=os.environ.get("API_HOST", "127.0.0.1"),
        api_port=_env_int("API_PORT", 8000),
        radar_min_avg_amount=_env_float("RADAR_MIN_AVG_AMOUNT", 50_000_000.0),
    )


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return build_settings()


def reload_settings() -> Settings:
    get_settings.cache_clear()
    return get_settings()
