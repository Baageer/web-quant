"""行情服务：指数、涨跌分布、成交额、行业强弱、榜单。"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Iterable

import numpy as np
import pandas as pd

from app.core.config import Settings
from app.core.errors import DataNotReadyError, InvalidParamError
from app.data.calendar import TradeCalendar
from app.data.frames import coerce_numeric
from app.data.repository import Repository
from app.data.universe import limit_threshold
from app.models.schemas import (
    AmountInfo,
    Breadth,
    ConfigData,
    DataStatus,
    DatasetStatus,
    Envelope,
    IndexQuote,
    Leaders,
    MarketOverview,
    RankItem,
    Regime,
    SectorItem,
    SectorList,
    TickerList,
)
from app.services.refresh_service import INDEX_CODES, RefreshService

SORT_FIELDS = {
    "pctChg": "pct_chg",
    "amount": "amount",
    "close": "close",
    "turnoverRate": "turnover_rate",
    "volumeRatio": "volume_ratio",
    "totalMv": "total_mv",
}


@dataclass
class MarketContext:
    trade_date: str
    daily: pd.DataFrame
    daily_basic: pd.DataFrame
    stock_basic: pd.DataFrame
    index_daily: pd.DataFrame
    limit_list: pd.DataFrame
    moneyflow: pd.DataFrame
    origin: str
    warnings: list[str] = field(default_factory=list)


class MarketService:
    def __init__(
        self,
        settings: Settings,
        repository: Repository,
        calendar: TradeCalendar,
        refresh: RefreshService | None = None,
    ) -> None:
        self._settings = settings
        self._repository = repository
        self._calendar = calendar
        self._refresh = refresh

    # ---- 公共工具 ----

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

    def _load(self, trade_date: str) -> MarketContext:
        warnings: list[str] = []
        daily_loaded = self._repository.read_dataset("daily", trade_date)

        if (daily_loaded is None) and self._can_fetch():
            assert self._refresh is not None
            self._refresh.ensure_dataset("daily", trade_date)
            daily_loaded = self._repository.read_dataset("daily", trade_date)

        if daily_loaded is None:
            raise DataNotReadyError(f"{trade_date} 的日线数据尚未就绪")

        daily = coerce_numeric(daily_loaded.frame)
        origin = daily_loaded.origin
        if origin == "sample":
            warnings.append("当前使用离线样本数据，仅用于功能演示，不构成投资建议")

        def load_optional(dataset: str, *, may_fetch: bool = True) -> tuple[pd.DataFrame, str | None]:
            loaded = self._repository.read_dataset(dataset, trade_date)
            if loaded is None and may_fetch and self._can_fetch():
                assert self._refresh is not None
                self._refresh.ensure_dataset(dataset, trade_date)
                loaded = self._repository.read_dataset(dataset, trade_date)
            if loaded is None:
                return pd.DataFrame(), None
            return coerce_numeric(loaded.frame), loaded.origin

        daily_basic, _ = load_optional("daily_basic")
        index_daily, _ = load_optional("index_daily")
        limit_list, _ = load_optional("limit_list", may_fetch=False)
        moneyflow, _ = load_optional("moneyflow", may_fetch=False)
        stock_loaded = self._repository.read_stock_basic()
        stock_basic = stock_loaded.frame if stock_loaded is not None else pd.DataFrame()

        if daily_basic.empty:
            warnings.append("缺少 daily_basic，换手率与量比不可用")
        if index_daily.empty:
            warnings.append("缺少指数日线，指数卡片为空")
        if limit_list.empty:
            warnings.append("缺少涨跌停数据，涨跌停家数按涨跌幅阈值近似")
        if stock_basic.empty:
            warnings.append("缺少 stock_basic，行业与名称信息不可用")

        return MarketContext(
            trade_date=trade_date,
            daily=daily,
            daily_basic=daily_basic,
            stock_basic=stock_basic,
            index_daily=index_daily,
            limit_list=limit_list,
            moneyflow=moneyflow,
            origin=origin,
            warnings=warnings,
        )

    def _can_fetch(self) -> bool:
        return (
            self._refresh is not None
            and self._settings.network_enabled
            and self._settings.token_configured
        )

    def _envelope(self, data, context: MarketContext):
        expected = self._calendar.latest_trade_date()
        return Envelope(
            ok=True,
            source="sample" if context.origin == "sample" else "snapshot",
            as_of=datetime.now().astimezone().isoformat(timespec="seconds"),
            stale=context.trade_date < expected,
            warnings=_unique(context.warnings),
            data=data,
        )

    def _prepare(self, context: MarketContext) -> pd.DataFrame:
        frame = context.daily.copy()
        if not context.daily_basic.empty:
            frame = frame.merge(
                context.daily_basic,
                on=["ts_code", "trade_date"],
                how="left",
                suffixes=("", "_basic"),
            )
        if not context.stock_basic.empty:
            columns = [
                col
                for col in ("ts_code", "name", "industry", "market", "list_date")
                if col in context.stock_basic.columns
            ]
            frame = frame.merge(context.stock_basic[columns], on="ts_code", how="left")

        frame["pct_chg"] = pd.to_numeric(frame["pct_chg"], errors="coerce").fillna(0.0)
        frame["amount"] = pd.to_numeric(frame["amount"], errors="coerce").fillna(0.0)
        frame["close"] = pd.to_numeric(frame["close"], errors="coerce")

        thresholds = [
            limit_threshold(code, name)
            for code, name in zip(frame["ts_code"], frame.get("name", frame["ts_code"]))
        ]
        frame["limit_threshold"] = np.array(thresholds) * 100.0
        frame["is_limit_up"] = frame["pct_chg"] >= frame["limit_threshold"] - 0.2
        frame["is_limit_down"] = frame["pct_chg"] <= -frame["limit_threshold"] + 0.2
        return frame

    # ---- 行情接口 ----

    def get_overview(self, date: str | None = None) -> Envelope[MarketOverview]:
        trade_date = self.resolve_trade_date(date)
        context = self._load(trade_date)
        frame = self._prepare(context)

        breadth = self._breadth(frame)
        amount_total = float(frame["amount"].sum()) / 1e5
        ratio_5d = self._amount_ratio_5d(trade_date, amount_total)
        sectors_top, sectors_bottom = self._sectors(frame)
        indices = self._indices(context, trade_date)

        leaders = Leaders(
            gainers=self._rank(frame.sort_values("pct_chg", ascending=False).head(10)),
            losers=self._rank(frame.sort_values("pct_chg", ascending=True).head(10)),
            turnover=self._rank(frame.sort_values("amount", ascending=False).head(10)),
        )

        index_pct = float(np.mean([item.pct_chg for item in indices])) if indices else 0.0
        regime = self._regime(breadth, index_pct, ratio_5d)

        data = MarketOverview(
            trade_date=trade_date,
            indices=indices,
            breadth=breadth,
            amount=AmountInfo(total=round(amount_total, 2), ratio_5d=round(ratio_5d, 4)),
            sectors_top=sectors_top,
            sectors_bottom=sectors_bottom,
            leaders=leaders,
            regime=regime,
        )
        return self._envelope(data, context)

    def get_tickers(
        self,
        date: str | None = None,
        *,
        sort: str = "pctChg",
        order: str = "desc",
        limit: int = 100,
        offset: int = 0,
        search: str | None = None,
    ) -> Envelope[TickerList]:
        if sort not in SORT_FIELDS:
            raise InvalidParamError(f"不支持的排序字段：{sort}")
        if order not in {"asc", "desc"}:
            raise InvalidParamError(f"不支持的排序方向：{order}")
        limit = max(1, min(int(limit), 500))
        offset = max(0, int(offset))

        trade_date = self.resolve_trade_date(date)
        context = self._load(trade_date)
        frame = self._prepare(context)

        if search:
            keyword = search.strip().upper()
            mask = frame["ts_code"].astype(str).str.upper().str.contains(keyword, na=False)
            if "name" in frame.columns:
                mask = mask | frame["name"].astype(str).str.contains(search.strip(), na=False)
            frame = frame[mask]

        column = SORT_FIELDS[sort]
        if column not in frame.columns:
            frame[column] = np.nan
        ascending = order == "asc"
        frame = frame.sort_values(column, ascending=ascending, na_position="last")

        total = int(len(frame))
        page = frame.iloc[offset : offset + limit]
        data = TickerList(
            trade_date=trade_date,
            total=total,
            items=self._rank(page),
        )
        return self._envelope(data, context)

    def get_breadth(self, date: str | None = None) -> Envelope[Breadth]:
        trade_date = self.resolve_trade_date(date)
        context = self._load(trade_date)
        data = self._breadth(self._prepare(context))
        return self._envelope(data, context)

    def get_sectors(
        self, date: str | None = None, *, limit: int = 10
    ) -> Envelope[SectorList]:
        trade_date = self.resolve_trade_date(date)
        context = self._load(trade_date)
        top, bottom = self._sectors(self._prepare(context), limit=max(1, min(limit, 30)))
        return self._envelope(SectorList(trade_date=trade_date, top=top, bottom=bottom), context)

    # ---- 运维接口 ----

    def get_status(self) -> DataStatus:
        datasets = [
            DatasetStatus(**item) for item in self._repository.dataset_statuses()
        ]
        return DataStatus(
            mode=self._settings.mode.value,
            expected_trade_date=self._calendar.latest_trade_date(),
            token_configured=self._settings.token_configured,
            calendar_fallback=self._calendar.is_fallback,
            datasets=datasets,
            recent_runs=self._repository.recent_refresh_runs(limit=10),
        )

    def get_config(self) -> ConfigData:
        latest = self._repository.latest_date("daily") or self._calendar.latest_trade_date()
        stock_basic = self._repository.read_stock_basic()
        universe = len(stock_basic.frame) if stock_basic is not None else 0
        return ConfigData(
            mode=self._settings.mode.value,
            network_enabled=self._settings.network_enabled,
            token_configured=self._settings.token_configured,
            latest_trade_date=latest,
            universe_size=universe,
        )

    # ---- 计算细节 ----

    def _breadth(self, frame: pd.DataFrame) -> Breadth:
        pct = frame["pct_chg"].astype(float)
        limit_up = int(frame["is_limit_up"].sum())
        limit_down = int(frame["is_limit_down"].sum())
        return Breadth(
            up=int((pct > 0).sum()),
            down=int((pct < 0).sum()),
            flat=int((pct == 0).sum()),
            limit_up=limit_up,
            limit_down=limit_down,
            total=int(len(frame)),
            median_pct=round(float(pct.median()), 2) if len(frame) else 0.0,
        )

    def _amount_ratio_5d(self, trade_date: str, today_total: float) -> float:
        available = [day for day in self._repository.available_dates("daily") if day < trade_date]
        previous = available[-5:]
        totals: list[float] = []
        for day in previous:
            loaded = self._repository.read_dataset("daily", day)
            if loaded is None or loaded.frame.empty:
                continue
            totals.append(float(pd.to_numeric(loaded.frame["amount"], errors="coerce").sum()) / 1e5)
        if not totals:
            return 0.0
        average = float(np.mean(totals))
        if average <= 0:
            return 0.0
        return (today_total / average) - 1.0

    def _indices(self, context: MarketContext, trade_date: str) -> list[IndexQuote]:
        if context.index_daily.empty:
            return []
        frame = context.index_daily
        frame = frame[frame["trade_date"].astype(str) == trade_date]
        quotes: list[IndexQuote] = []
        for code, name in INDEX_CODES.items():
            row = frame[frame["ts_code"] == code]
            if row.empty:
                continue
            item = row.iloc[0]
            quotes.append(
                IndexQuote(
                    code=code,
                    name=name,
                    close=round(float(item["close"]), 2),
                    pct_chg=round(float(item["pct_chg"]), 2),
                    amount=round(float(item["amount"]) / 1e5, 2)
                    if "amount" in row.columns
                    else 0.0,
                )
            )
        return quotes

    def _sectors(self, frame: pd.DataFrame, limit: int = 10) -> tuple[list[SectorItem], list[SectorItem]]:
        if "industry" not in frame.columns:
            return [], []
        valid = frame.dropna(subset=["industry"])
        valid = valid[valid["industry"].astype(str).str.strip() != ""]
        if valid.empty:
            return [], []
        grouped = valid.groupby("industry").agg(
            pct_chg=("pct_chg", "median"),
            amount=("amount", "sum"),
            count=("ts_code", "count"),
        )
        grouped = grouped.reset_index()
        grouped["amount"] = grouped["amount"] / 1e5
        grouped = grouped.sort_values("pct_chg", ascending=False)

        def to_items(part: pd.DataFrame) -> list[SectorItem]:
            return [
                SectorItem(
                    name=str(row["industry"]),
                    pct_chg=round(float(row["pct_chg"]), 2),
                    count=int(row["count"]),
                    amount=round(float(row["amount"]), 2),
                )
                for _, row in part.iterrows()
            ]

        top = to_items(grouped.head(limit))
        bottom = to_items(grouped.sort_values("pct_chg", ascending=True).head(limit))
        return top, bottom

    def _rank(self, frame: pd.DataFrame) -> list[RankItem]:
        items: list[RankItem] = []
        for _, row in frame.iterrows():
            items.append(
                RankItem(
                    ts_code=str(row.get("ts_code", "")),
                    name=str(row.get("name") or row.get("ts_code") or ""),
                    industry=_optional_str(row.get("industry")),
                    close=_optional_float(row.get("close")) or 0.0,
                    pct_chg=round(_optional_float(row.get("pct_chg")) or 0.0, 2),
                    amount=round((_optional_float(row.get("amount")) or 0.0) / 1e5, 2),
                    turnover_rate=_optional_float(row.get("turnover_rate")),
                    volume_ratio=_optional_float(row.get("volume_ratio")),
                )
            )
        return items

    def _regime(self, breadth: Breadth, index_pct: float, amount_ratio: float) -> Regime:
        total = max(breadth.total, 1)
        breadth_score = (breadth.up - breadth.down) / total
        index_score = index_pct / 2.0
        amount_score = max(min(amount_ratio, 0.5), -0.5)
        score = 0.5 * breadth_score + 0.3 * index_score + 0.2 * amount_score
        if score > 0.15:
            label = "risk_on"
        elif score < -0.15:
            label = "risk_off"
        else:
            label = "neutral"
        reasons = [
            f"上涨 {breadth.up} 家 / 下跌 {breadth.down} 家",
            f"指数平均涨跌幅 {index_pct:+.2f}%",
            f"成交额较 5 日均值 {amount_ratio * 100:+.1f}%",
        ]
        return Regime(label=label, score=round(float(score), 4), reasons=reasons)


def _optional_float(value) -> float | None:
    if value is None:
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if np.isnan(number) or np.isinf(number):
        return None
    return number


def _optional_str(value) -> str | None:
    if value is None:
        return None
    if isinstance(value, float) and np.isnan(value):
        return None
    text = str(value).strip()
    return text or None


def _unique(items: Iterable[str]) -> list[str]:
    seen: set[str] = set()
    output: list[str] = []
    for item in items:
        if item not in seen:
            seen.add(item)
            output.append(item)
    return output
