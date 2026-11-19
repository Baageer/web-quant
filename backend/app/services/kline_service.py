"""K 线服务：历史读取、复权、周期重采样、指标计算与缓存。

对应 DESIGN.md 8.8 / 9.4 / 10.3。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Iterable

import numpy as np
import pandas as pd

from app.core.cache import TTLCache
from app.core.config import Settings
from app.core.errors import (
    DataNotReadyError,
    InsufficientHistoryError,
    InvalidParamError,
    SymbolNotFoundError,
)
from app.core.logging import get_logger
from app.data.adjust import apply_adjust
from app.data.calendar import TradeCalendar
from app.data.frames import coerce_numeric
from app.data.repository import Repository
from app.domain.indicators import MA_WINDOWS, compute_indicators, series_to_list
from app.models.schemas import (
    BollSeries,
    Envelope,
    KlineBar,
    KlineData,
    KlineIndicators,
    KdjSeries,
    MacdSeries,
    MaSeries,
    VolMaSeries,
)

logger = get_logger(__name__)

PERIODS = ("daily", "weekly", "monthly")
ADJUSTS = ("raw", "qfq", "hfq")
MAX_HISTORY_DAYS = 600
MIN_BARS = {"daily": 60, "weekly": 20, "monthly": 8}
CACHE_TTL = 600


@dataclass
class KlineContext:
    trade_date: str
    history: pd.DataFrame
    stock_basic: pd.DataFrame
    origin: str
    warnings: list[str] = field(default_factory=list)


class KlineService:
    def __init__(
        self,
        settings: Settings,
        repository: Repository,
        calendar: TradeCalendar,
    ) -> None:
        self._settings = settings
        self._repository = repository
        self._calendar = calendar
        self._cache = TTLCache(maxsize=48)

    # ---- 日期与历史 ----

    def resolve_trade_date(self, date: str | None = None) -> str:
        available = self._repository.available_dates("daily")
        if not available:
            raise DataNotReadyError("本地没有日线数据，请先运行 scripts/bootstrap_data.py")
        if date:
            target = self._calendar.clamp(date)
            candidates = [day for day in available if day <= target]
            if not candidates:
                raise DataNotReadyError(f"{date} 之前没有可用日线数据")
            return max(candidates)
        return max(available)

    def load_context(self, date: str | None = None) -> KlineContext:
        trade_date = self.resolve_trade_date(date)
        key = ("history", trade_date)
        cached = self._cache.get(key)
        if cached is not None:
            return cached

        warnings: list[str] = []
        available = [
            day for day in self._repository.available_dates("daily") if day <= trade_date
        ][-MAX_HISTORY_DAYS:]
        frames: list[pd.DataFrame] = []
        origin = "snapshot"
        for day in available:
            loaded = self._repository.read_dataset("daily", day)
            if loaded is None or loaded.frame.empty:
                continue
            frame = coerce_numeric(loaded.frame)
            factors = self._repository.read_dataset("adj_factor", day)
            if factors is not None and not factors.frame.empty:
                frame = frame.merge(
                    coerce_numeric(factors.frame)[["ts_code", "trade_date", "adj_factor"]],
                    on=["ts_code", "trade_date"],
                    how="left",
                )
            frames.append(frame)
            if day == available[-1]:
                origin = loaded.origin

        if not frames:
            raise DataNotReadyError(f"{trade_date} 的日线数据尚未就绪")

        history = pd.concat(frames, ignore_index=True)
        history["trade_date"] = history["trade_date"].astype(str)
        history = history.sort_values(["ts_code", "trade_date"]).reset_index(drop=True)

        stock_loaded = self._repository.read_stock_basic()
        stock_basic = stock_loaded.frame if stock_loaded is not None else pd.DataFrame()
        if origin == "sample":
            warnings.append("当前使用离线样本数据，仅用于功能演示，不构成投资建议")
        if stock_basic.empty:
            warnings.append("缺少 stock_basic，股票名称不可用")
        if "adj_factor" not in history.columns or history["adj_factor"].isna().all():
            warnings.append("缺少复权因子，复权价格与原始价一致")

        context = KlineContext(
            trade_date=trade_date,
            history=history,
            stock_basic=stock_basic,
            origin=origin,
            warnings=warnings,
        )
        self._cache.set(key, context, CACHE_TTL)
        return context

    def adjusted_history(self, context: KlineContext, mode: str = "qfq") -> pd.DataFrame:
        """全市场复权历史，供信号服务做横截面计算。"""
        key = ("adjusted", context.trade_date, mode)
        cached = self._cache.get(key)
        if cached is not None:
            return cached
        frame = apply_adjust(context.history, None, mode)
        self._cache.set(key, frame, CACHE_TTL)
        return frame

    # ---- K 线接口 ----

    def get_kline(
        self,
        ts_code: str,
        *,
        period: str = "daily",
        adjust: str = "qfq",
        limit: int = 250,
        date: str | None = None,
    ) -> Envelope[KlineData]:
        if period not in PERIODS:
            raise InvalidParamError(f"不支持的周期：{period}")
        if adjust not in ADJUSTS:
            raise InvalidParamError(f"不支持的复权方式：{adjust}")
        limit = max(20, min(int(limit), 1000))

        context = self.load_context(date)
        code = self.resolve_code(ts_code, context.stock_basic)
        cache_key = ("kline", code, period, adjust, context.trade_date, limit)
        cached: KlineData | None = self._cache.get(cache_key)
        if cached is not None:
            return self._envelope(cached, context)

        code_frame = context.history[
            context.history["ts_code"].astype(str).str.upper() == code
        ]
        if code_frame.empty:
            raise SymbolNotFoundError(f"{code} 在 {context.trade_date} 没有可用数据")

        adjusted = apply_adjust(code_frame, None, adjust)
        bars = resample_bars(adjusted, period)
        minimum = MIN_BARS[period]
        if len(bars) < minimum:
            raise InsufficientHistoryError(
                f"{code} 的{period} K 线仅有 {len(bars)} 根，至少需要 {minimum} 根",
                detail={"bars": len(bars), "required": minimum, "period": period},
            )

        indicators = compute_indicators(bars)
        tail = bars.tail(limit).reset_index(drop=True)
        data = KlineData(
            ts_code=code,
            name=self._name_of(code, context.stock_basic),
            period=period,
            adjust=adjust,
            bars=[self._bar(row) for _, row in tail.iterrows()],
            indicators=KlineIndicators(
                ma=MaSeries(
                    **{
                        f"ma{window}": series_to_list(
                            indicators["ma"][f"ma{window}"].tail(limit)
                        )
                        for window in MA_WINDOWS
                    }
                ),
                boll=BollSeries(
                    upper=series_to_list(indicators["boll"]["upper"].tail(limit)),
                    middle=series_to_list(indicators["boll"]["middle"].tail(limit)),
                    lower=series_to_list(indicators["boll"]["lower"].tail(limit)),
                ),
                macd=MacdSeries(
                    diff=series_to_list(indicators["macd"]["diff"].tail(limit)),
                    dea=series_to_list(indicators["macd"]["dea"].tail(limit)),
                    hist=series_to_list(indicators["macd"]["hist"].tail(limit)),
                ),
                rsi=series_to_list(indicators["rsi"].tail(limit)),
                atr=series_to_list(indicators["atr"].tail(limit)),
                kdj=KdjSeries(
                    k=series_to_list(indicators["kdj"]["k"].tail(limit)),
                    d=series_to_list(indicators["kdj"]["d"].tail(limit)),
                    j=series_to_list(indicators["kdj"]["j"].tail(limit)),
                ),
                vol_ma=VolMaSeries(
                    ma5=series_to_list(indicators["vol_ma"]["ma5"].tail(limit)),
                    ma20=series_to_list(indicators["vol_ma"]["ma20"].tail(limit)),
                ),
            ),
        )
        self._cache.set(cache_key, data, CACHE_TTL)
        return self._envelope(data, context)

    # ---- 组装 ----

    def _envelope(self, data: KlineData, context: KlineContext) -> Envelope[KlineData]:
        warnings = list(context.warnings)
        if len(data.bars) < 60:
            warnings.append("历史 K 线不足 60 根，MA60 等长周期指标可能为空")
        expected = self._calendar.latest_trade_date()
        return Envelope(
            ok=True,
            source="sample" if context.origin == "sample" else "snapshot",
            as_of=datetime.now().astimezone().isoformat(timespec="seconds"),
            stale=context.trade_date < expected,
            warnings=_unique(warnings),
            data=data,
        )

    @staticmethod
    def _bar(row: pd.Series) -> KlineBar:
        raw_date = str(row["trade_date"])
        display_date = (
            f"{raw_date[:4]}-{raw_date[4:6]}-{raw_date[6:8]}"
            if len(raw_date) == 8 and raw_date.isdigit()
            else raw_date
        )
        return KlineBar(
            date=display_date,
            open=round(float(row["open"]), 2),
            high=round(float(row["high"]), 2),
            low=round(float(row["low"]), 2),
            close=round(float(row["close"]), 2),
            volume=_number(row.get("vol")),
            amount=_number(row.get("amount")),
            pct_chg=round(_number(row.get("pct_chg")), 2),
        )

    @staticmethod
    def _name_of(code: str, stock_basic: pd.DataFrame) -> str:
        if stock_basic.empty or "name" not in stock_basic.columns:
            return code
        matched = stock_basic[stock_basic["ts_code"].astype(str).str.upper() == code]
        if matched.empty:
            return code
        name = matched.iloc[0].get("name")
        return str(name) if name else code

    @staticmethod
    def resolve_code(ts_code: str, stock_basic: pd.DataFrame) -> str:
        code = (ts_code or "").strip().upper()
        if not code:
            raise InvalidParamError("股票代码不能为空")
        if "." in code:
            return code
        if not stock_basic.empty and "symbol" in stock_basic.columns:
            matched = stock_basic[stock_basic["symbol"].astype(str) == code]
            if not matched.empty:
                return str(matched.iloc[0]["ts_code"]).upper()
        return code


def resample_bars(bars: pd.DataFrame, period: str) -> pd.DataFrame:
    """按周期聚合；输入必须已复权且按日期升序。"""
    frame = bars.sort_values("trade_date").reset_index(drop=True)
    if period == "daily":
        return frame

    dates = pd.to_datetime(frame["trade_date"], format="%Y%m%d", errors="coerce")
    if period == "weekly":
        frame["_period"] = dates.dt.strftime("%G-%V")
    else:
        frame["_period"] = dates.dt.strftime("%Y-%m")

    grouped = (
        frame.groupby("_period", as_index=False, sort=True)
        .agg(
            trade_date=("trade_date", "last"),
            open=("open", "first"),
            high=("high", "max"),
            low=("low", "min"),
            close=("close", "last"),
            pre_close=("pre_close", "first"),
            vol=("vol", "sum"),
            amount=("amount", "sum"),
        )
        .sort_values("trade_date")
        .reset_index(drop=True)
    )
    previous = grouped["close"].shift(1)
    first_base = grouped["pre_close"].replace(0.0, np.nan)
    pct = (grouped["close"] / previous - 1.0) * 100.0
    pct = pct.where(previous.notna(), (grouped["close"] / first_base - 1.0) * 100.0)
    grouped["pct_chg"] = pct
    grouped["adj_factor"] = 1.0
    return grouped


def _number(value) -> float:
    if value is None:
        return 0.0
    try:
        number = float(value)
    except (TypeError, ValueError):
        return 0.0
    if np.isnan(number) or np.isinf(number):
        return 0.0
    return round(number, 2)


def _unique(items: Iterable[str]) -> list[str]:
    seen: set[str] = set()
    output: list[str] = []
    for item in items:
        if item not in seen:
            seen.add(item)
            output.append(item)
    return output
