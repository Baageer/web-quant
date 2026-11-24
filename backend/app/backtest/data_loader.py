"""面板装载与按日切片（见 docs/BACKTEST_DESIGN.md §4）。

对外只暴露 `panel.as_of(date)`，任何晚于 date 的行都不可见，从结构上防止前视。
复权口径：信号用 hfq、成交与估值用 raw、展示用 qfq（回测内部不生成 qfq）。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import numpy as np
import pandas as pd

from app.core.errors import DataNotReadyError
from app.core.logging import get_logger
from app.data.adjust import apply_adjust
from app.data.calendar import TradeCalendar
from app.data.frames import coerce_numeric
from app.data.repository import Repository
from app.data.universe import is_st_name
from app.backtest.models import BacktestConfig, UniverseConfig

logger = get_logger(__name__)

PRICE_FIELDS = ("open", "high", "low", "close", "pre_close")


@dataclass
class BarContext:
    """策略可见的截面：只包含 <= date 的数据。"""

    date: str
    panel: "Panel"

    @property
    def symbols(self) -> list[str]:
        return self.panel.symbols

    def closes(self, ts_code: str, *, adjusted: bool = True) -> pd.Series:
        column = "close_hfq" if adjusted else "close"
        series = self.panel.pivot[column].get(ts_code)
        if series is None:
            return pd.Series(dtype="float64")
        return series.loc[: self.date].dropna()

    def history(self, ts_code: str) -> pd.DataFrame:
        frame = self.panel.by_code.get(ts_code)
        if frame is None or frame.empty:
            return pd.DataFrame()
        return frame[frame["trade_date"] <= self.date]

    def is_tradable(self, ts_code: str) -> bool:
        return self.panel.is_tradable(self.date, ts_code)

    def history_window(self, days: int = 90) -> pd.DataFrame:
        """截至 date 的最近 days 个交易日长表（按日期升序）。"""
        return self.panel.window_bars(days, self.date)

    def daily_basic(self) -> pd.DataFrame:
        return self.panel.daily_basic.get(self.date, pd.DataFrame())

    def moneyflow(self) -> pd.DataFrame:
        return self.panel.moneyflow.get(self.date, pd.DataFrame())

    def index_return_20(self) -> float | None:
        return self.panel.index_returns.get(self.date)

    def index_closes(self) -> pd.Series:
        """截至 date 的沪深 300 收盘价（含回测窗口前的预热点）。"""
        return self.panel.index_closes.loc[: self.date]

    def stock_basic(self) -> pd.DataFrame:
        return self.panel.stock_basic

    def trading_age(self, ts_code: str) -> int:
        return int(self.panel.trading_age.get(ts_code, 0))


class Panel:
    """横截面面板：长表 + 按日期/代码的透视缓存。"""

    def __init__(
        self,
        bars: pd.DataFrame,
        dates: list[str],
        symbols: list[str],
        pivot: dict[str, pd.DataFrame],
        tradable: pd.DataFrame,
        *,
        warnings: list[str] | None = None,
        origin: str = "test",
        daily_basic: dict[str, pd.DataFrame] | None = None,
        moneyflow: dict[str, pd.DataFrame] | None = None,
        index_returns: dict[str, float] | None = None,
        stock_basic: pd.DataFrame | None = None,
        trading_age: dict[str, int] | None = None,
        benchmark: pd.DataFrame | None = None,
        index_closes: pd.Series | None = None,
        adj_missing_rate: float = 0.0,
    ) -> None:
        self.bars = bars
        self.dates = dates
        self.symbols = symbols
        self.pivot = pivot
        self.tradable = tradable
        self.warnings = list(warnings or [])
        self.origin = origin
        self.daily_basic = daily_basic or {}
        self.moneyflow = moneyflow or {}
        self.index_returns = index_returns or {}
        self.index_closes = (
            index_closes if index_closes is not None else pd.Series(dtype="float64")
        )
        self.trading_age = trading_age or {}
        self.benchmark = (
            benchmark
            if benchmark is not None and not benchmark.empty
            else pd.DataFrame(columns=["date", "close"])
        )
        self.adj_missing_rate = float(adj_missing_rate)
        self.by_code: dict[str, pd.DataFrame] = {
            code: frame for code, frame in bars.groupby("ts_code")
        }
        self._date_spans: dict[str, tuple[int, int]] = self._build_date_spans(bars)
        if stock_basic is None or stock_basic.empty:
            columns = [
                column
                for column in ("ts_code", "name", "industry")
                if column in bars.columns
            ]
            stock_basic = bars[columns].drop_duplicates("ts_code") if columns else pd.DataFrame()
        self.stock_basic = stock_basic
        if "industry" in stock_basic.columns:
            self.industry_map = {
                str(row["ts_code"]): str(row["industry"]).strip()
                for _, row in stock_basic.iterrows()
                if str(row.get("industry") or "").strip()
            }
        else:
            self.industry_map = {}

    @property
    def stale_data(self) -> bool:
        """复权因子覆盖率低于 99% 时标记数据陈旧（见 §4.3）。"""
        return self.adj_missing_rate > 0.01

    def window_bars(self, days: int, date: str) -> pd.DataFrame:
        """截至 date 的最近 days 个交易日长表，按全局行号切片避免整表拷贝。"""
        window = [day for day in self.dates if day <= date][-max(1, days):]
        if not window:
            return self.bars.iloc[0:0]
        start = self._date_spans.get(window[0])
        last = self._date_spans.get(window[-1])
        if start is not None and last is not None:
            return self.bars.iloc[start[0] : last[1]]
        return self.bars[self.bars["trade_date"].isin(window)]

    @staticmethod
    def _build_date_spans(bars: pd.DataFrame) -> dict[str, tuple[int, int]]:
        """行情按 trade_date 全局排序时，记录每个交易日的行区间。"""
        if bars.empty:
            return {}
        values = bars["trade_date"].astype(str).to_numpy()
        if len(values) > 1 and not (values[1:] >= values[:-1]).all():
            return {}
        starts = np.flatnonzero(np.r_[True, values[1:] != values[:-1]])
        ends = np.r_[starts[1:], len(values)]
        return {
            str(values[start]): (int(start), int(end))
            for start, end in zip(starts, ends)
        }

    # ---- 构造 ----

    @classmethod
    def from_bars(
        cls,
        bars: pd.DataFrame,
        *,
        universe: UniverseConfig | None = None,
        trading_age: dict[str, int] | None = None,
        warnings: list[str] | None = None,
        origin: str = "test",
        daily_basic: dict[str, pd.DataFrame] | None = None,
        moneyflow: dict[str, pd.DataFrame] | None = None,
        index_returns: dict[str, float] | None = None,
        stock_basic: pd.DataFrame | None = None,
        benchmark: pd.DataFrame | None = None,
        index_closes: pd.Series | None = None,
        adj_missing_rate: float = 0.0,
    ) -> "Panel":
        if bars.empty:
            raise DataNotReadyError("回测面板为空，缺少日线数据")

        frame = bars.copy()
        frame["trade_date"] = frame["trade_date"].astype(str)
        frame = frame.drop_duplicates(subset=["ts_code", "trade_date"], keep="last")
        frame = frame.sort_values(["trade_date", "ts_code"]).reset_index(drop=True)
        for column in ("open", "high", "low", "close", "pre_close", "vol", "amount"):
            if column not in frame.columns:
                frame[column] = float("nan")

        if "close_hfq" not in frame.columns:
            adjusted = apply_adjust(frame, None, "hfq")
            for column in PRICE_FIELDS:
                frame[f"{column}_hfq"] = adjusted[column].astype(float)
        if "adj_factor" not in frame.columns:
            frame["adj_factor"] = 1.0

        dates = sorted(frame["trade_date"].unique().tolist())
        symbols = sorted(frame["ts_code"].unique().tolist())

        pivot: dict[str, pd.DataFrame] = {}
        for column in ("open", "high", "low", "close", "pre_close", "vol", "amount", "open_hfq", "close_hfq", "adj_factor"):
            pivot[column] = (
                frame.pivot(index="trade_date", columns="ts_code", values=column)
                .sort_index()
                .reindex(index=dates, columns=symbols)
            )

        tradable = cls._build_tradable(frame, dates, symbols, universe, trading_age)
        return cls(
            bars=frame,
            dates=dates,
            symbols=symbols,
            pivot=pivot,
            tradable=tradable,
            warnings=warnings,
            origin=origin,
            daily_basic=daily_basic,
            moneyflow=moneyflow,
            index_returns=index_returns,
            stock_basic=stock_basic,
            trading_age=trading_age,
            benchmark=benchmark,
            index_closes=index_closes,
            adj_missing_rate=adj_missing_rate,
        )

    @staticmethod
    def _build_tradable(
        frame: pd.DataFrame,
        dates: list[str],
        symbols: list[str],
        universe: UniverseConfig | None,
        trading_age: dict[str, int] | None,
    ) -> pd.DataFrame:
        mask = pd.DataFrame(True, index=dates, columns=symbols)
        if universe is None:
            return mask

        # 20 日均额（daily.amount 单位为千元）
        amount = (
            frame.pivot(index="trade_date", columns="ts_code", values="amount")
            .sort_index()
            .reindex(index=dates, columns=symbols)
        )
        if universe.min_avg_amount > 0:
            avg_amount = amount.rolling(
                window=max(1, universe.avg_window), min_periods=1
            ).mean()
            mask &= (avg_amount * 1000.0) >= universe.min_avg_amount

        if universe.exclude_st and "name" in frame.columns:
            names = frame.drop_duplicates("ts_code").set_index("ts_code")["name"]
            st_codes = [code for code in symbols if is_st_name(names.get(code))]
            if st_codes:
                mask[st_codes] = False

        if universe.min_list_days > 0 and trading_age:
            for code in symbols:
                if int(trading_age.get(code, 0)) < universe.min_list_days:
                    mask[code] = False

        # 无 bar 的日期不视为可买（停牌另由 constraints 判定）
        mask &= amount.notna()
        return mask.astype(bool)

    # ---- 访问 ----

    def bar(self, date: str, ts_code: str) -> pd.Series | None:
        frame = self.by_code.get(ts_code)
        if frame is None or frame.empty:
            return None
        matched = frame[frame["trade_date"] == date]
        if matched.empty:
            return None
        return matched.iloc[-1]

    def adj_factor(self, date: str, ts_code: str) -> float | None:
        table = self.pivot.get("adj_factor")
        if table is None or date not in table.index:
            return None
        try:
            value = table.at[date, ts_code]
        except KeyError:
            return None
        if pd.isna(value) or float(value) <= 0:
            return None
        return float(value)

    def atr(self, date: str, ts_code: str, window: int = 14) -> float | None:
        """单只股票截至 date 的 ATR（真实波幅均值），用于 ATR 止盈。"""
        close_table = self.pivot.get("close")
        high_table = self.pivot.get("high")
        low_table = self.pivot.get("low")
        if (
            close_table is None
            or high_table is None
            or low_table is None
            or ts_code not in close_table.columns
        ):
            return None
        try:
            position = close_table.index.get_loc(date)
        except KeyError:
            return None
        if position < window:
            return None
        start = position - window
        closes = close_table.iloc[start : position + 1][ts_code].to_numpy(dtype=float)
        highs = high_table.iloc[start : position + 1][ts_code].to_numpy(dtype=float)
        lows = low_table.iloc[start : position + 1][ts_code].to_numpy(dtype=float)
        if np.isnan(closes).any() or np.isnan(highs).any() or np.isnan(lows).any():
            return None
        previous = closes[:-1]
        true_range = np.maximum.reduce(
            [
                highs[1:] - lows[1:],
                np.abs(highs[1:] - previous),
                np.abs(lows[1:] - previous),
            ]
        )
        if true_range.size == 0:
            return None
        return float(true_range.mean())

    def open_prices(self, date: str) -> dict[str, float]:
        return self._prices("open", date)

    def close_prices(self, date: str) -> dict[str, float]:
        return self._prices("close", date)

    def _prices(self, column: str, date: str) -> dict[str, float]:
        table = self.pivot[column]
        if date not in table.index:
            return {}
        row = table.loc[date].dropna()
        return {code: float(value) for code, value in row.items() if float(value) > 0}

    def is_tradable(self, date: str, ts_code: str) -> bool:
        if date not in self.tradable.index or ts_code not in self.tradable.columns:
            return False
        return bool(self.tradable.at[date, ts_code])

    def as_of(self, date: str) -> BarContext:
        return BarContext(date=date, panel=self)


def _listing_age(calendar: TradeCalendar, list_date: str, start_date: str) -> int:
    """上市交易日数。

    离线样本的 trade_cal 只覆盖回测窗口时，日历测不出 2010 年上市的年龄；
    此时上市日早于日历起点，按「已上市很久」处理，避免次新股过滤误杀全市场。
    """
    days = calendar.days
    listed = list_date.replace("-", "")
    if days and listed < days[0]:
        return max(calendar.trading_age(days[0], start_date), 0) + 10_000
    return calendar.trading_age(listed, start_date)


def load_panel(
    repository: Repository,
    calendar: TradeCalendar,
    config: BacktestConfig,
    *,
    need_flow: bool = True,
) -> Panel:
    """按起止日期装载日线、复权因子与股票基础信息，并做股票池过滤。"""
    warnings: list[str] = []
    available = repository.available_dates("daily")
    dates = [day for day in available if config.start_date <= day <= config.end_date]
    if not dates:
        raise DataNotReadyError(
            f"{config.start_date}~{config.end_date} 没有可用日线数据，请先准备本地数据"
        )
    if dates[0] > config.start_date or dates[-1] < config.end_date:
        warnings.append(
            f"回测区间按可用数据裁剪为 {dates[0]}~{dates[-1]}（请求 {config.start_date}~{config.end_date}）"
        )

    frames: list[pd.DataFrame] = []
    origins: set[str] = set()
    daily_basic: dict[str, pd.DataFrame] = {}
    moneyflow: dict[str, pd.DataFrame] = {}
    for day in dates:
        loaded = repository.read_dataset("daily", day)
        if loaded is None or loaded.frame.empty:
            warnings.append(f"{day} 日线缺失，已跳过")
            continue
        frame = coerce_numeric(loaded.frame)
        factors = repository.read_dataset("adj_factor", day)
        if factors is not None and not factors.frame.empty:
            frame = frame.merge(
                coerce_numeric(factors.frame)[["ts_code", "trade_date", "adj_factor"]],
                on=["ts_code", "trade_date"],
                how="left",
            )
        frame["trade_date"] = day
        frames.append(frame)
        origins.add(loaded.origin)

        if need_flow:
            basic = repository.read_dataset("daily_basic", day)
            if basic is not None and not basic.frame.empty:
                daily_basic[day] = _narrow(
                    coerce_numeric(basic.frame),
                    ("ts_code", "turnover_rate", "volume_ratio", "pe_ttm", "circ_mv"),
                )
            flow = repository.read_dataset("moneyflow", day)
            if flow is not None and not flow.frame.empty:
                moneyflow[day] = _narrow(
                    coerce_numeric(flow.frame), ("ts_code", "net_mf_amount")
                )

    if not frames:
        raise DataNotReadyError("日线读取失败，回测面板为空")

    history = pd.concat(frames, ignore_index=True)
    adj_missing_rate = 0.0
    if "adj_factor" not in history.columns:
        warnings.append("缺少复权因子，信号价与原始价一致")
    else:
        adj_missing_rate = float(history["adj_factor"].isna().mean())
        if adj_missing_rate > 0.01:
            warnings.append("复权因子存在缺失，按前后值填充")

    if config.symbols:
        wanted = {code.strip().upper() for code in config.symbols if code and code.strip()}
        history = history[history["ts_code"].astype(str).str.upper().isin(wanted)]
        missing = sorted(wanted - set(history["ts_code"].astype(str).str.upper()))
        if missing:
            warnings.append(f"指定标的缺少数据：{', '.join(missing[:10])}")
        if history.empty:
            raise DataNotReadyError("指定标的在回测区间内没有数据")

    stock_basic = repository.read_stock_basic()
    trading_age: dict[str, int] = {}
    basic_frame = pd.DataFrame()
    if stock_basic is None or stock_basic.frame.empty:
        warnings.append("缺少 stock_basic，ST 过滤与行业信息不可用")
    else:
        basic = stock_basic.frame
        basic_frame = basic.copy()
        columns = [
            col
            for col in ("ts_code", "name", "industry", "list_date")
            if col in basic.columns
        ]
        history = history.merge(basic[columns], on="ts_code", how="left")
        if "list_date" in history.columns:
            listed = (
                history.drop_duplicates("ts_code")
                .set_index("ts_code")["list_date"]
                .dropna()
            )
            trading_age = {
                str(code): _listing_age(calendar, str(day), config.start_date)
                for code, day in listed.items()
            }

    origin = "sample" if "sample" in origins else "raw"
    if origin == "sample":
        warnings.append("当前使用离线样本数据，回测结果仅用于功能验证")
    if need_flow and not daily_basic:
        warnings.append("缺少 daily_basic，换手率与量比按量能近似")
    if need_flow and not moneyflow:
        warnings.append("缺少 moneyflow，资金流因子按中性处理")

    index_prices = _load_index_closes(repository, dates, warnings)
    index_returns = _load_index_returns(index_prices, dates)
    benchmark = _benchmark_frame(index_prices, dates)

    return Panel.from_bars(
        history,
        universe=config.universe,
        trading_age=trading_age,
        warnings=warnings,
        origin=origin,
        daily_basic=daily_basic,
        moneyflow=moneyflow,
        index_returns=index_returns,
        stock_basic=basic_frame,
        benchmark=benchmark,
        index_closes=index_prices,
        adj_missing_rate=adj_missing_rate,
    )


INDEX_CODE = "000300.SH"


def _narrow(frame: pd.DataFrame, columns: tuple[str, ...]) -> pd.DataFrame:
    """只保留策略真正使用的列，降低多年回测的常驻内存。"""
    keep = [column for column in columns if column in frame.columns]
    return frame[keep].copy()


def _load_index_closes(
    repository: Repository,
    dates: list[str],
    warnings: list[str],
) -> pd.Series:
    """读取回测窗口前后的沪深 300 收盘价，供相对强度、基准对比与市场状态判断使用。"""
    available = [day for day in repository.available_dates("index_daily") if day <= dates[-1]]
    window = available[-(len(dates) + 280):]
    closes: dict[str, float] = {}
    for day in window:
        loaded = repository.read_dataset("index_daily", day)
        if loaded is None or loaded.frame.empty:
            continue
        rows = loaded.frame[
            loaded.frame["ts_code"].astype(str) == INDEX_CODE
        ]
        if rows.empty:
            continue
        value = pd.to_numeric(rows.iloc[0]["close"], errors="coerce")
        if pd.notna(value) and float(value) > 0:
            closes[day] = float(value)
    if len(closes) < 21:
        warnings.append("缺少沪深300 指数数据，相对强度因子按中性处理")
        return pd.Series(dtype="float64")
    return pd.Series(closes).sort_index()


def _load_index_returns(prices: pd.Series, dates: list[str]) -> dict[str, float]:
    """计算每个交易日沪深 300 的 20 日收益，用于相对强度因子。"""
    if prices.empty:
        return {}
    returns = prices / prices.shift(20) - 1.0
    return {
        day: float(returns[day])
        for day in dates
        if day in returns.index and pd.notna(returns[day])
    }


def _benchmark_frame(prices: pd.Series, dates: list[str]) -> pd.DataFrame:
    """回测区间内的基准收盘价序列（用于 metrics 基准对比）。"""
    if prices.empty:
        return pd.DataFrame(columns=["date", "close"])
    wanted = [day for day in dates if day in prices.index]
    return pd.DataFrame(
        {"date": wanted, "close": [float(prices[day]) for day in wanted]}
    )
