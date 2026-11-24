"""本地数据仓储：Parquet 分区 + SQLite 元数据。

目录约定：
  data/raw/tushare/bars_by_date/YYYY/YYYYMMDD.parquet
  data/raw/tushare/daily_basic/YYYY/YYYYMMDD.parquet
  data/raw/tushare/adj_factor/YYYY/YYYYMMDD.parquet
  data/raw/tushare/index_daily/YYYYMMDD.parquet
  data/raw/tushare/moneyflow/YYYY/YYYYMMDD.parquet
  data/raw/tushare/limit_list/YYYYMMDD.parquet
  data/raw/tushare/stock_basic.parquet
  data/raw/tushare/trade_cal.parquet
  data/samples/tushare/...            # 同构离线样本
"""

from __future__ import annotations

import os
import sqlite3
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

import pandas as pd

from app.core.logging import get_logger
from app.data.quality import validate_dataset

logger = get_logger(__name__)

# dataset -> (目录名, 是否按年分子目录, 是否允许为空)
DATASET_LAYOUT: dict[str, tuple[str, bool, bool]] = {
    "daily": ("bars_by_date", True, False),
    "daily_basic": ("daily_basic", True, False),
    "adj_factor": ("adj_factor", True, False),
    "moneyflow": ("moneyflow", True, True),
    "limit_list": ("limit_list", False, True),
    "index_daily": ("index_daily", False, False),
    "suspend": ("suspend", False, True),
    "fina_indicator": ("fina_indicator", True, True),
}

RAW_ORIGIN = "tushare"
SAMPLE_ORIGIN = "sample"


@dataclass(frozen=True)
class LoadedDataset:
    dataset: str
    trade_date: str
    frame: pd.DataFrame
    origin: str
    path: Path


class Repository:
    def __init__(self, data_dir: Path, db_path: Path, samples_dir: Path | None = None) -> None:
        self.data_dir = Path(data_dir)
        self.raw_dir = self.data_dir / "raw" / "tushare"
        self.samples_dir = Path(samples_dir) if samples_dir else self.data_dir / "samples"
        self.sample_dir = self.samples_dir / "tushare"
        self.db_path = Path(db_path)
        self._ensure_dirs()
        self._init_db()

    # ---- 基础设施 ----

    def _ensure_dirs(self) -> None:
        for path in (self.raw_dir, self.sample_dir, self.db_path.parent):
            path.mkdir(parents=True, exist_ok=True)

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path, timeout=30)
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA synchronous=NORMAL")
        return conn

    def _init_db(self) -> None:
        with self._connect() as conn:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS dataset_manifest (
                    dataset    TEXT NOT NULL,
                    trade_date TEXT NOT NULL,
                    layer      TEXT NOT NULL,
                    path       TEXT NOT NULL,
                    rows       INTEGER NOT NULL,
                    complete   INTEGER NOT NULL,
                    reason     TEXT,
                    origin     TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    PRIMARY KEY (dataset, trade_date, layer)
                );

                CREATE TABLE IF NOT EXISTS refresh_runs (
                    id          TEXT PRIMARY KEY,
                    dataset     TEXT NOT NULL,
                    trade_date  TEXT,
                    status      TEXT NOT NULL,
                    error       TEXT,
                    started_at  TEXT NOT NULL,
                    finished_at TEXT
                );

                CREATE TABLE IF NOT EXISTS trade_calendar (
                    cal_date TEXT PRIMARY KEY,
                    is_open  INTEGER NOT NULL
                );
                """
            )

    # ---- 路径 ----

    def _root(self, layer: str) -> Path:
        return self.raw_dir if layer == "raw" else self.sample_dir

    def dataset_path(self, dataset: str, trade_date: str, layer: str = "raw") -> Path:
        if dataset not in DATASET_LAYOUT:
            raise ValueError(f"unknown dataset: {dataset}")
        name, by_year, _ = DATASET_LAYOUT[dataset]
        root = self._root(layer) / name
        if by_year:
            return root / trade_date[:4] / f"{trade_date}.parquet"
        return root / f"{trade_date}.parquet"

    def stock_basic_path(self, layer: str = "raw") -> Path:
        return self._root(layer) / "stock_basic.parquet"

    def trade_cal_path(self, layer: str = "raw") -> Path:
        return self._root(layer) / "trade_cal.parquet"

    # ---- 读取 ----

    def read_dataset(
        self,
        dataset: str,
        trade_date: str,
        *,
        include_sample: bool = True,
    ) -> LoadedDataset | None:
        for layer, origin in (("raw", RAW_ORIGIN), ("sample", SAMPLE_ORIGIN)):
            if layer == "sample" and not include_sample:
                break
            path = self.dataset_path(dataset, trade_date, layer)
            if not path.exists():
                continue
            try:
                frame = pd.read_parquet(path)
            except Exception as exc:  # 损坏文件不应阻塞其他数据集
                logger.warning("读取 %s 失败：%s", path, exc)
                continue
            return LoadedDataset(dataset, trade_date, frame, origin, path)
        return None

    def read_stock_basic(self) -> LoadedDataset | None:
        for layer, origin in (("raw", RAW_ORIGIN), ("sample", SAMPLE_ORIGIN)):
            path = self.stock_basic_path(layer)
            if path.exists():
                return LoadedDataset("stock_basic", "", pd.read_parquet(path), origin, path)
        return None

    def read_trade_cal(self) -> LoadedDataset | None:
        for layer, origin in (("raw", RAW_ORIGIN), ("sample", SAMPLE_ORIGIN)):
            path = self.trade_cal_path(layer)
            if path.exists():
                return LoadedDataset("trade_cal", "", pd.read_parquet(path), origin, path)
        return None

    # ---- 写入 ----

    def write_dataset(
        self,
        dataset: str,
        trade_date: str,
        frame: pd.DataFrame,
        *,
        layer: str = "raw",
        origin: str = RAW_ORIGIN,
        allow_empty: bool | None = None,
        force: bool = False,
    ) -> tuple[bool, str | None, Path]:
        if dataset not in DATASET_LAYOUT:
            raise ValueError(f"unknown dataset: {dataset}")
        if allow_empty is None:
            allow_empty = DATASET_LAYOUT[dataset][2]

        complete, reason = validate_dataset(
            dataset,
            frame,
            trade_date,
            allow_empty=allow_empty,
            allow_small=(layer == "sample"),
        )
        path = self.dataset_path(dataset, trade_date, layer)
        if not complete and not force:
            logger.warning("%s %s 校验失败，不写入：%s", dataset, trade_date, reason)
            return False, reason, path

        path.parent.mkdir(parents=True, exist_ok=True)
        tmp_path = path.with_suffix(f".tmp{os.getpid()}")
        frame.to_parquet(tmp_path, index=False)
        os.replace(tmp_path, path)

        self.record_manifest(
            dataset=dataset,
            trade_date=trade_date,
            layer=layer,
            path=path,
            rows=len(frame),
            complete=complete,
            reason=reason,
            origin=origin,
        )
        return complete, reason, path

    def write_stock_basic(
        self,
        frame: pd.DataFrame,
        *,
        layer: str = "raw",
        origin: str = RAW_ORIGIN,
    ) -> Path:
        path = self.stock_basic_path(layer)
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp_path = path.with_suffix(f".tmp{os.getpid()}")
        frame.to_parquet(tmp_path, index=False)
        os.replace(tmp_path, path)
        return path

    def write_trade_cal(
        self,
        frame: pd.DataFrame,
        *,
        layer: str = "raw",
        origin: str = RAW_ORIGIN,
    ) -> Path:
        path = self.trade_cal_path(layer)
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp_path = path.with_suffix(f".tmp{os.getpid()}")
        frame.to_parquet(tmp_path, index=False)
        os.replace(tmp_path, path)

        open_days = frame[frame["is_open"].astype(int) == 1]["cal_date"].astype(str)
        with self._connect() as conn:
            conn.executemany(
                "INSERT OR REPLACE INTO trade_calendar(cal_date, is_open) VALUES (?, 1)",
                [(day,) for day in open_days],
            )
        return path

    # ---- manifest ----

    def record_manifest(
        self,
        *,
        dataset: str,
        trade_date: str,
        layer: str,
        path: Path,
        rows: int,
        complete: bool,
        reason: str | None,
        origin: str,
    ) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                INSERT OR REPLACE INTO dataset_manifest
                (dataset, trade_date, layer, path, rows, complete, reason, origin, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    dataset,
                    trade_date,
                    layer,
                    str(path),
                    int(rows),
                    1 if complete else 0,
                    reason,
                    origin,
                    datetime.now().isoformat(timespec="seconds"),
                ),
            )

    def clear_manifest_layer(self, layer: str) -> None:
        with self._connect() as conn:
            conn.execute("DELETE FROM dataset_manifest WHERE layer = ?", (layer,))

    def get_manifest(self, dataset: str, trade_date: str) -> dict | None:
        with self._connect() as conn:
            row = conn.execute(
                """
                SELECT dataset, trade_date, layer, path, rows, complete, reason, origin, updated_at
                FROM dataset_manifest
                WHERE dataset = ? AND trade_date = ?
                ORDER BY complete DESC, layer ASC
                LIMIT 1
                """,
                (dataset, trade_date),
            ).fetchone()
        if row is None:
            return None
        keys = [
            "dataset",
            "trade_date",
            "layer",
            "path",
            "rows",
            "complete",
            "reason",
            "origin",
            "updated_at",
        ]
        return dict(zip(keys, row))

    def latest_date(self, dataset: str) -> str | None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT MAX(trade_date) FROM dataset_manifest WHERE dataset = ?",
                (dataset,),
            ).fetchone()
        if row and row[0]:
            return str(row[0])

        dates: list[str] = []
        for layer in ("raw", "sample"):
            root = self._root(layer) / DATASET_LAYOUT[dataset][0]
            if not root.exists():
                continue
            for path in root.rglob("*.parquet"):
                stem = path.stem
                if len(stem) == 8 and stem.isdigit():
                    dates.append(stem)
        return max(dates) if dates else None

    def available_dates(self, dataset: str) -> list[str]:
        dates: set[str] = set()
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT DISTINCT trade_date FROM dataset_manifest WHERE dataset = ?",
                (dataset,),
            ).fetchall()
        dates.update(str(row[0]) for row in rows if row[0])
        for layer in ("raw", "sample"):
            root = self._root(layer) / DATASET_LAYOUT[dataset][0]
            if not root.exists():
                continue
            for path in root.rglob("*.parquet"):
                stem = path.stem
                if len(stem) == 8 and stem.isdigit():
                    dates.add(stem)
        return sorted(dates)

    def dataset_statuses(self) -> list[dict]:
        statuses: list[dict] = []
        for dataset in DATASET_LAYOUT:
            latest = self.latest_date(dataset)
            manifest = self.get_manifest(dataset, latest) if latest else None
            statuses.append(
                {
                    "dataset": dataset,
                    "latest_date": latest,
                    "complete": bool(manifest["complete"]) if manifest else bool(latest),
                    "origin": manifest["origin"] if manifest else ("sample" if latest else None),
                    "rows": manifest["rows"] if manifest else None,
                    "reason": manifest["reason"] if manifest else None,
                }
            )
        stock_basic = self.read_stock_basic()
        statuses.append(
            {
                "dataset": "stock_basic",
                "latest_date": None,
                "complete": stock_basic is not None,
                "origin": stock_basic.origin if stock_basic else None,
                "rows": len(stock_basic.frame) if stock_basic else None,
                "reason": None,
            }
        )
        return statuses

    # ---- 刷新运行记录 ----

    def record_refresh_start(self, run_id: str, dataset: str, trade_date: str | None) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                INSERT OR REPLACE INTO refresh_runs
                (id, dataset, trade_date, status, started_at)
                VALUES (?, ?, ?, 'running', ?)
                """,
                (run_id, dataset, trade_date, datetime.now().isoformat(timespec="seconds")),
            )

    def record_refresh_finish(
        self, run_id: str, status: str, error: str | None = None
    ) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                UPDATE refresh_runs
                SET status = ?, error = ?, finished_at = ?
                WHERE id = ?
                """,
                (status, error, datetime.now().isoformat(timespec="seconds"), run_id),
            )

    def recent_refresh_runs(self, limit: int = 20) -> list[dict]:
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT id, dataset, trade_date, status, error, started_at, finished_at
                FROM refresh_runs
                ORDER BY started_at DESC
                LIMIT ?
                """,
                (limit,),
            ).fetchall()
        keys = ["id", "dataset", "tradeDate", "status", "error", "startedAt", "finishedAt"]
        return [dict(zip(keys, row)) for row in rows]
