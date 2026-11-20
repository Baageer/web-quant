"""回测产物落盘与读取（见 docs/BACKTEST_DESIGN.md §10.1）。

目录：data/backtest/runs/<run_id>/
  manifest.json / metrics.json / nav.parquet / trades.parquet / positions.parquet
"""

from __future__ import annotations

import json
import re
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any

import pandas as pd

from app.core.logging import get_logger
from app.backtest.metrics import json_safe
from app.backtest.models import (
    NAV_COLUMNS,
    POSITION_COLUMNS,
    TRADE_COLUMNS,
    BacktestResult,
)

logger = get_logger(__name__)

RUN_ID_PATTERN = re.compile(r"^[A-Za-z0-9_-]{4,64}$")
RUN_FILES = ("manifest.json", "metrics.json", "nav.parquet", "trades.parquet", "positions.parquet")


class ResultStore:
    def __init__(self, root: Path) -> None:
        self.root = Path(root)
        self.runs_dir = self.root / "runs"
        self.runs_dir.mkdir(parents=True, exist_ok=True)

    # ---- 路径 ----

    def run_dir(self, run_id: str) -> Path:
        if not RUN_ID_PATTERN.match(str(run_id)):
            raise ValueError(f"非法 run_id：{run_id}")
        return self.runs_dir / run_id

    def exists(self, run_id: str) -> bool:
        try:
            return (self.run_dir(run_id) / "manifest.json").exists()
        except ValueError:
            return False

    @staticmethod
    def new_run_id() -> str:
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        return f"bt_{stamp}_{uuid.uuid4().hex[:6]}"

    # ---- 写入 ----

    def write_manifest(self, run_id: str, manifest: dict[str, Any]) -> Path:
        """写入/更新 manifest（异步任务在运行前后都会调用）。"""
        target = self.run_dir(run_id)
        target.mkdir(parents=True, exist_ok=True)
        self._write_json(target / "manifest.json", manifest)
        return target

    def save(self, result: BacktestResult) -> Path:
        target = self.run_dir(result.run_id)
        target.mkdir(parents=True, exist_ok=True)

        self._write_json(target / "manifest.json", result.manifest)
        self._write_json(target / "metrics.json", result.metrics)
        self._write_frame(
            target / "nav.parquet", result.nav, NAV_COLUMNS
        )
        self._write_frame(
            target / "trades.parquet", result.trades, TRADE_COLUMNS
        )
        self._write_frame(
            target / "positions.parquet", result.positions, POSITION_COLUMNS
        )
        logger.info("回测结果已写入 %s", target)
        return target

    @staticmethod
    def _write_json(path: Path, payload: dict[str, Any]) -> None:
        tmp = path.with_suffix(".json.tmp")
        tmp.write_text(
            json.dumps(json_safe(payload), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        tmp.replace(path)

    @staticmethod
    def _write_frame(path: Path, frame: pd.DataFrame, columns: list[str]) -> None:
        output = frame.copy()
        if output.empty and not list(output.columns):
            output = pd.DataFrame(columns=columns)
        else:
            for column in columns:
                if column not in output.columns:
                    output[column] = None
            output = output[columns]
        output.to_parquet(path, index=False)

    # ---- 读取 ----

    def load_manifest(self, run_id: str) -> dict | None:
        return self._read_json(self.run_dir(run_id) / "manifest.json")

    def load_metrics(self, run_id: str) -> dict | None:
        return self._read_json(self.run_dir(run_id) / "metrics.json")

    def write_audit(self, run_id: str, audit: dict[str, Any]) -> Path:
        target = self.run_dir(run_id)
        target.mkdir(parents=True, exist_ok=True)
        path = target / "audit.json"
        self._write_json(path, audit)
        return path

    def load_audit(self, run_id: str) -> dict | None:
        return self._read_json(self.run_dir(run_id) / "audit.json")

    def load_nav(self, run_id: str) -> pd.DataFrame:
        return self._read_frame(self.run_dir(run_id) / "nav.parquet", NAV_COLUMNS)

    def load_trades(self, run_id: str) -> pd.DataFrame:
        return self._read_frame(self.run_dir(run_id) / "trades.parquet", TRADE_COLUMNS)

    def load_positions(self, run_id: str) -> pd.DataFrame:
        return self._read_frame(self.run_dir(run_id) / "positions.parquet", POSITION_COLUMNS)

    @staticmethod
    def _read_json(path: Path) -> dict | None:
        if not path.exists():
            return None
        return json.loads(path.read_text(encoding="utf-8"))

    @staticmethod
    def _read_frame(path: Path, columns: list[str]) -> pd.DataFrame:
        if not path.exists():
            return pd.DataFrame(columns=columns)
        return pd.read_parquet(path)

    def list_runs(self, *, limit: int = 20, offset: int = 0) -> tuple[list[dict], int]:
        manifests: list[dict] = []
        for directory in self.runs_dir.iterdir():
            if not directory.is_dir():
                continue
            manifest = self._read_json(directory / "manifest.json")
            if manifest:
                manifests.append(manifest)
        manifests.sort(key=lambda item: str(item.get("created_at", "")), reverse=True)
        total = len(manifests)
        start = max(0, int(offset))
        return manifests[start : start + max(1, int(limit))], total
