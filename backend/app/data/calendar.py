"""A 股交易日历。

优先读取 data/raw|samples/tushare/trade_cal.parquet，缺失时按工作日兜底。
"""

from __future__ import annotations

import bisect
from datetime import date, datetime, time, timedelta

import pandas as pd

from app.core.logging import get_logger
from app.data.repository import Repository

logger = get_logger(__name__)

# 收盘后留出 10 分钟给数据落地
DATA_READY_TIME = time(15, 10)
FALLBACK_START = date(2015, 1, 1)


class TradeCalendar:
    def __init__(self, repository: Repository) -> None:
        self._repository = repository
        self._days: list[str] = []
        self._day_set: set[str] = set()
        self._is_fallback = False
        self.reload()

    def reload(self) -> None:
        loaded = self._repository.read_trade_cal()
        if loaded is not None and not loaded.frame.empty:
            frame = loaded.frame
            open_days = frame[frame["is_open"].astype(int) == 1]["cal_date"].astype(str)
            self._days = sorted(open_days.tolist())
            self._day_set = set(self._days)
            self._is_fallback = False
            return

        logger.warning("未找到交易日历，按周一至周五兜底")
        self._is_fallback = True
        today = date.today()
        days: list[str] = []
        cursor = FALLBACK_START
        while cursor <= today + timedelta(days=370):
            if cursor.weekday() < 5:
                days.append(cursor.strftime("%Y%m%d"))
            cursor += timedelta(days=1)
        self._days = days
        self._day_set = set(days)

    @property
    def is_fallback(self) -> bool:
        return self._is_fallback

    @property
    def days(self) -> list[str]:
        return self._days

    def is_trade_day(self, day: str) -> bool:
        return day in self._day_set

    def latest_trade_date(self, now: datetime | None = None) -> str:
        now = now or datetime.now()
        today = now.strftime("%Y%m%d")
        if self.is_trade_day(today) and now.time() >= DATA_READY_TIME:
            return today
        return self.previous(today)

    def previous(self, day: str, steps: int = 1) -> str:
        index = bisect.bisect_left(self._days, day) - 1
        if index < 0:
            return self._days[0] if self._days else day
        return self._days[max(index - steps + 1, 0)]

    def next(self, day: str, steps: int = 1) -> str:
        index = bisect.bisect_right(self._days, day)
        if index >= len(self._days):
            return self._days[-1] if self._days else day
        return self._days[min(index + steps - 1, len(self._days) - 1)]

    def clamp(self, day: str) -> str:
        """把任意日期对齐到最近一个交易日（不晚于该日期）。"""
        text = day.replace("-", "")
        if self.is_trade_day(text):
            return text
        index = bisect.bisect_left(self._days, text) - 1
        if index < 0:
            return text
        return self._days[index]

    def trading_age(self, start_day: str, end_day: str) -> int:
        if not start_day:
            return 0
        start = start_day.replace("-", "")
        return max(bisect.bisect_right(self._days, end_day) - bisect.bisect_left(self._days, start), 0)

    def to_frame(self) -> pd.DataFrame:
        return pd.DataFrame({"cal_date": self._days, "is_open": 1})
