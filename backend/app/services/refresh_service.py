"""数据刷新服务：从 TuShare 拉取并落盘。"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Iterable, Sequence

import pandas as pd

from app.core.config import Settings
from app.core.errors import ModeNotAllowedError, TushareConfigError
from app.core.logging import get_logger
from app.data.calendar import TradeCalendar
from app.data.frames import coerce_numeric
from app.data.repository import Repository
from app.data.tushare_client import TushareClient

logger = get_logger(__name__)

INDEX_CODES = {
    "000001.SH": "上证指数",
    "399001.SZ": "深证成指",
    "399006.SZ": "创业板指",
    "000300.SH": "沪深300",
    "000905.SH": "中证500",
    "000852.SH": "中证1000",
}

DEFAULT_DATASETS = ("daily", "daily_basic", "adj_factor", "index_daily")
OPTIONAL_DATASETS = ("moneyflow", "limit_list")


class RefreshService:
    def __init__(
        self,
        settings: Settings,
        repository: Repository,
        calendar: TradeCalendar,
        client: TushareClient | None = None,
    ) -> None:
        self._settings = settings
        self._repository = repository
        self._calendar = calendar
        self._client = client

    def _get_client(self) -> TushareClient:
        if not self._settings.network_enabled:
            raise ModeNotAllowedError("当前为 offline 模式，禁止访问 TuShare")
        if not self._settings.token_configured:
            raise TushareConfigError("未配置 TS_TOKEN")
        if self._client is None:
            self._client = TushareClient(
                self._settings.ts_token,
                calls_per_minute=self._settings.calls_per_min,
                max_retries=self._settings.max_retries,
                timeout=self._settings.timeout_seconds,
            )
        return self._client

    # ---- 基础数据 ----

    def refresh_stock_basic(self) -> dict:
        client = self._get_client()
        frames = []
        for status in ("L", "D", "P"):
            frame = client.stock_basic(status)
            if frame is not None and not frame.empty:
                frame["list_status"] = frame.get("list_status", status)
                frames.append(frame)
        if not frames:
            return {"dataset": "stock_basic", "status": "failed", "rows": 0}
        merged = pd.concat(frames, ignore_index=True).drop_duplicates("ts_code", keep="last")
        self._repository.write_stock_basic(merged)
        return {"dataset": "stock_basic", "status": "ok", "rows": int(len(merged))}

    def refresh_trade_cal(self, start_date: str, end_date: str) -> dict:
        client = self._get_client()
        frame = client.trade_cal(start_date, end_date)
        if frame is None or frame.empty:
            return {"dataset": "trade_cal", "status": "failed", "rows": 0}
        self._repository.write_trade_cal(frame)
        self._calendar.reload()
        return {"dataset": "trade_cal", "status": "ok", "rows": int(len(frame))}

    # ---- 日频数据 ----

    def refresh_date(
        self,
        trade_date: str,
        datasets: Sequence[str] | None = None,
        *,
        force: bool = False,
    ) -> list[dict]:
        if not self._settings.network_enabled:
            raise ModeNotAllowedError("当前为 offline 模式，禁止刷新数据")
        target = self._calendar.clamp(trade_date)
        names = tuple(datasets) if datasets else DEFAULT_DATASETS
        results: list[dict] = []
        for dataset in names:
            if dataset not in DEFAULT_DATASETS and dataset not in OPTIONAL_DATASETS:
                results.append(
                    {"dataset": dataset, "status": "failed", "error": "unknown dataset"}
                )
                continue
            results.append(self._refresh_one(dataset, target, force=force))
        return results

    def _refresh_one(self, dataset: str, trade_date: str, *, force: bool) -> dict:
        existing = self._repository.get_manifest(dataset, trade_date)
        if (
            not force
            and existing
            and existing["layer"] == "raw"
            and bool(existing["complete"])
        ):
            return {
                "dataset": dataset,
                "status": "skipped",
                "rows": existing["rows"],
                "complete": True,
            }

        run_id = uuid.uuid4().hex
        self._repository.record_refresh_start(run_id, dataset, trade_date)
        try:
            frame = self._fetch(dataset, trade_date)
            frame = coerce_numeric(frame)
            complete, reason, path = self._repository.write_dataset(
                dataset, trade_date, frame
            )
            status = "ok" if complete else "incomplete"
            self._repository.record_refresh_finish(run_id, status, reason)
            return {
                "dataset": dataset,
                "status": status,
                "rows": int(len(frame)),
                "complete": complete,
                "reason": reason,
                "path": str(path),
            }
        except Exception as exc:
            self._repository.record_refresh_finish(run_id, "failed", str(exc))
            logger.exception("%s %s 刷新失败", dataset, trade_date)
            return {"dataset": dataset, "status": "failed", "error": str(exc)}

    def _fetch(self, dataset: str, trade_date: str) -> pd.DataFrame:
        client = self._get_client()
        if dataset == "daily":
            return client.daily(trade_date)
        if dataset == "daily_basic":
            return client.daily_basic(trade_date)
        if dataset == "adj_factor":
            return client.adj_factor(trade_date)
        if dataset == "index_daily":
            frames = [
                client.index_daily(code, trade_date, trade_date)
                for code in INDEX_CODES
            ]
            frames = [frame for frame in frames if frame is not None and not frame.empty]
            return pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()
        if dataset == "moneyflow":
            return client.moneyflow(trade_date)
        if dataset == "limit_list":
            frames = [
                client.limit_list(trade_date, limit_type)
                for limit_type in ("U", "D")
            ]
            frames = [frame for frame in frames if frame is not None and not frame.empty]
            return pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()
        raise ValueError(f"unknown dataset: {dataset}")

    def ensure_dataset(self, dataset: str, trade_date: str) -> bool:
        """本地缺失时尝试补数；失败返回 False，由调用方决定是否继续降级。"""
        if not self._settings.network_enabled or not self._settings.token_configured:
            return False
        try:
            result = self._refresh_one(dataset, trade_date, force=False)
        except Exception as exc:
            logger.warning("%s %s 补数失败：%s", dataset, trade_date, exc)
            return False
        return result.get("status") in {"ok", "skipped"}

    def bootstrap(
        self,
        start_date: str,
        end_date: str,
        datasets: Sequence[str] | None = None,
        *,
        force: bool = False,
    ) -> list[dict]:
        if not self._settings.network_enabled:
            raise ModeNotAllowedError("当前为 offline 模式，禁止初始化数据")
        names = tuple(datasets) if datasets else DEFAULT_DATASETS
        results: list[dict] = []
        days = [
            day
            for day in self._calendar.days
            if start_date <= day <= end_date
        ]
        total = len(days)
        for index, day in enumerate(days, start=1):
            logger.info("bootstrap %s/%s %s", index, total, day)
            results.extend(self.refresh_date(day, names, force=force))
        return results
