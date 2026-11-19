"""机会雷达服务：加载横截面数据、过滤股票池、打分并输出可解释候选。

对应 DESIGN.md 8.7 / 9.3 / 10.2。
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime
from typing import Iterable

import numpy as np
import pandas as pd

from app.core.config import Settings
from app.core.errors import DataNotReadyError, InvalidParamError, SymbolNotFoundError
from app.core.logging import get_logger
from app.data.calendar import TradeCalendar
from app.data.frames import coerce_numeric
from app.data.repository import Repository
from app.data.universe import is_st_name
from app.models.schemas import (
    Envelope,
    FactorScore,
    RadarDetail,
    RadarOpportunity,
    RadarOpportunityList,
    TradePlan,
)
from app.services.refresh_service import RefreshService
from app.services.scoring import (
    DEFAULT_CONFIG,
    RadarConfig,
    build_reasons,
    build_risk_flags,
    build_trade_plan,
    compute_stock_features,
    factor_breakdown,
    score_cross_section,
)

logger = get_logger(__name__)

HISTORY_WINDOW = 90
INDEX_CODE = "000300.SH"


@dataclass
class RadarContext:
    trade_date: str
    history: pd.DataFrame
    daily_basic: pd.DataFrame
    moneyflow: pd.DataFrame
    limit_list: pd.DataFrame
    stock_basic: pd.DataFrame
    index_ret_20: float | None
    origin: str
    warnings: list[str] = field(default_factory=list)


class RadarService:
    def __init__(
        self,
        settings: Settings,
        repository: Repository,
        calendar: TradeCalendar,
        refresh: RefreshService | None = None,
        config: RadarConfig | None = None,
    ) -> None:
        self._settings = settings
        self._repository = repository
        self._calendar = calendar
        self._refresh = refresh
        self._config = config or DEFAULT_CONFIG

    # ---- 日期与数据加载 ----

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

    def _can_fetch(self) -> bool:
        return (
            self._refresh is not None
            and self._settings.network_enabled
            and self._settings.token_configured
        )

    def _load(self, trade_date: str) -> RadarContext:
        warnings: list[str] = []
        primary = self._repository.read_dataset("daily", trade_date)
        if primary is None and self._can_fetch():
            assert self._refresh is not None
            self._refresh.ensure_dataset("daily", trade_date)
            primary = self._repository.read_dataset("daily", trade_date)
        if primary is None:
            raise DataNotReadyError(f"{trade_date} 的日线数据尚未就绪")

        available = [
            day for day in self._repository.available_dates("daily") if day <= trade_date
        ]
        window = available[-HISTORY_WINDOW:]
        frames: list[pd.DataFrame] = []
        for day in window:
            loaded = (
                primary
                if day == trade_date
                else self._repository.read_dataset("daily", day)
            )
            if loaded is None or loaded.frame.empty:
                continue
            frames.append(coerce_numeric(loaded.frame))
        history = pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()

        def load_optional(dataset: str) -> pd.DataFrame:
            loaded = self._repository.read_dataset(dataset, trade_date)
            if loaded is None and self._can_fetch():
                assert self._refresh is not None
                self._refresh.ensure_dataset(dataset, trade_date)
                loaded = self._repository.read_dataset(dataset, trade_date)
            if loaded is None or loaded.frame.empty:
                return pd.DataFrame()
            return coerce_numeric(loaded.frame)

        daily_basic = load_optional("daily_basic")
        moneyflow = load_optional("moneyflow")
        limit_list = load_optional("limit_list")
        stock_loaded = self._repository.read_stock_basic()
        stock_basic = stock_loaded.frame if stock_loaded is not None else pd.DataFrame()

        origin = primary.origin
        if origin == "sample":
            warnings.append("当前使用离线样本数据，仅用于功能演示，不构成投资建议")
        if daily_basic.empty:
            warnings.append("缺少 daily_basic，换手率与量比按量能近似")
        if moneyflow.empty:
            warnings.append("缺少 moneyflow，资金流因子按中性处理")
        if limit_list.empty:
            warnings.append("缺少涨跌停数据，风险标记按涨跌幅阈值近似")
        if stock_basic.empty:
            warnings.append("缺少 stock_basic，名称、行业与上市天数不可用")

        index_ret = self._index_return(trade_date)
        if index_ret is None:
            warnings.append("缺少沪深300 指数数据，相对强度因子按中性处理")

        return RadarContext(
            trade_date=trade_date,
            history=history,
            daily_basic=daily_basic,
            moneyflow=moneyflow,
            limit_list=limit_list,
            stock_basic=stock_basic,
            index_ret_20=index_ret,
            origin=origin,
            warnings=warnings,
        )

    def _index_return(self, trade_date: str) -> float | None:
        available = [
            day for day in self._repository.available_dates("index_daily") if day <= trade_date
        ][-21:]
        closes: list[float] = []
        for day in available:
            loaded = self._repository.read_dataset("index_daily", day)
            if loaded is None or loaded.frame.empty:
                continue
            frame = loaded.frame
            row = frame[frame["ts_code"].astype(str) == INDEX_CODE]
            if row.empty:
                continue
            closes.append(float(pd.to_numeric(row.iloc[0]["close"], errors="coerce")))
        if len(closes) < 21 or closes[0] <= 0:
            return None
        return closes[-1] / closes[0] - 1.0

    # ---- 特征与筛选 ----

    def _features(self, context: RadarContext) -> pd.DataFrame:
        trading_age: dict[str, int] = {}
        stock_basic = context.stock_basic
        if not stock_basic.empty and "list_date" in stock_basic.columns:
            for _, row in stock_basic.iterrows():
                list_date = row.get("list_date")
                if list_date is None or (isinstance(list_date, float) and np.isnan(list_date)):
                    continue
                code = str(row.get("ts_code"))
                trading_age[code] = self._calendar.trading_age(str(list_date), context.trade_date)

        features = compute_stock_features(
            context.history,
            stock_basic=stock_basic,
            trading_age=trading_age,
        )
        if features.empty:
            return features

        if not context.daily_basic.empty:
            columns = [
                column
                for column in ("ts_code", "turnover_rate", "volume_ratio")
                if column in context.daily_basic.columns
            ]
            features = features.merge(
                context.daily_basic[columns].drop_duplicates("ts_code"),
                on="ts_code",
                how="left",
            )
        else:
            features["turnover_rate"] = np.nan
            features["volume_ratio"] = np.nan

        features["volume_ratio"] = features["volume_ratio"].fillna(
            features["volume_ratio_calc"]
        )

        if not context.moneyflow.empty:
            features = features.merge(
                context.moneyflow[["ts_code", "net_mf_amount"]].drop_duplicates("ts_code"),
                on="ts_code",
                how="left",
            )
        else:
            features["net_mf_amount"] = np.nan
        # net_mf_amount 单位万元，amount 单位千元
        features["capital_ratio"] = np.where(
            features["amount"] > 0,
            features["net_mf_amount"] * 10.0 / features["amount"],
            np.nan,
        )

        if context.index_ret_20 is None:
            features["rs_20"] = np.nan
        else:
            features["rs_20"] = features["ret_20"] - context.index_ret_20

        return features

    def _screen(
        self,
        features: pd.DataFrame,
        *,
        exclude_st: bool,
        min_amount: float,
    ) -> pd.DataFrame:
        out = features.copy()
        reason = pd.Series("", index=out.index, dtype="object")

        def mark(mask: pd.Series, text: str) -> None:
            target = mask.fillna(False) & (reason == "")
            reason[target] = text

        if exclude_st and "name" in out.columns:
            mark(out["name"].map(is_st_name), "ST 或退市整理")
        mark(
            (out["vol"].fillna(0) <= 0) | (out["amount"].fillna(0) <= 0),
            "当日停牌或无成交",
        )
        mark(
            out["list_days"].fillna(0) < self._config.min_list_days,
            f"上市不足 {self._config.min_list_days} 个交易日",
        )
        mark(
            out["close"].fillna(0) < self._config.min_price,
            f"股价低于 {self._config.min_price:.0f} 元",
        )
        mark(
            out["avg_amount_20"].fillna(0) * 1000.0 < min_amount,
            "20 日平均成交额不足",
        )

        out["exclude_reason"] = reason
        out["eligible"] = reason == ""
        return out

    def _data_complete(self, context: RadarContext) -> bool:
        return (
            not context.daily_basic.empty
            and not context.moneyflow.empty
            and context.index_ret_20 is not None
        )

    # ---- 对外接口 ----

    def get_opportunities(
        self,
        date: str | None = None,
        *,
        top_k: int = 50,
        min_amount: float | None = None,
        exclude_st: bool = True,
        industry: str | None = None,
    ) -> Envelope[RadarOpportunityList]:
        top_k = max(1, min(int(top_k), 200))
        if min_amount is not None and min_amount < 0:
            raise InvalidParamError("minAmount 不能为负数")

        trade_date = self.resolve_trade_date(date)
        context = self._load(trade_date)
        features = self._features(context)
        if features.empty:
            raise DataNotReadyError(f"{trade_date} 缺少可用于打分的横截面数据")

        threshold = (
            float(min_amount)
            if min_amount is not None
            else float(self._settings.radar_min_avg_amount)
        )
        screened = self._screen(features, exclude_st=exclude_st, min_amount=threshold)
        eligible = screened[screened["eligible"]].copy()
        scored = score_cross_section(
            eligible, config=self._config, data_complete=self._data_complete(context)
        )

        if industry:
            keyword = industry.strip()
            scored = scored[scored["industry"].fillna("").astype(str).str.contains(keyword)]

        scored = scored.sort_values(
            ["score", "confidence"], ascending=[False, False]
        )
        total = int(len(scored))
        selected = scored.head(top_k)

        items = [
            self._build_opportunity(row, rank)
            for rank, (_, row) in enumerate(selected.iterrows(), start=1)
        ]
        if top_k == 50 and min_amount is None and not industry:
            self._write_snapshot(trade_date, items)

        data = RadarOpportunityList(
            trade_date=trade_date,
            total=total,
            eligible=int(len(eligible)),
            items=items,
        )
        return self._envelope(data, context)

    def get_detail(self, ts_code: str, date: str | None = None) -> Envelope[RadarDetail]:
        code = (ts_code or "").strip().upper()
        if not code:
            raise InvalidParamError("股票代码不能为空")

        trade_date = self.resolve_trade_date(date)
        context = self._load(trade_date)
        features = self._features(context)
        if features.empty or not (features["ts_code"].astype(str).str.upper() == code).any():
            raise SymbolNotFoundError(f"{code} 在 {trade_date} 没有可用数据")

        screened = self._screen(
            features,
            exclude_st=True,
            min_amount=float(self._settings.radar_min_avg_amount),
        )
        row = self._row_for(screened, code)
        assert row is not None
        eligible = bool(row.get("eligible"))
        exclude_reason = None if eligible else str(row.get("exclude_reason") or "")

        base = screened[screened["eligible"]]
        scored = score_cross_section(
            base, config=self._config, data_complete=self._data_complete(context)
        )
        scored_row = self._row_for(scored, code)
        if scored_row is None:
            # 被硬过滤：用全量横截面给出参考评分，仅作解释用途
            scored = score_cross_section(
                screened, config=self._config, data_complete=self._data_complete(context)
            )
            scored_row = self._row_for(scored, code)
        if scored_row is None:
            raise SymbolNotFoundError(f"{code} 无法计算评分")

        opportunity = self._build_opportunity(scored_row, rank=0)
        detail = RadarDetail(
            **opportunity.model_dump(by_alias=False),
            eligible=eligible,
            exclude_reason=exclude_reason or None,
            raw_score=round(float(scored_row.get("raw_score") or 0.0), 2),
            risk_penalty=round(float(scored_row.get("risk_penalty") or 0.0), 2),
            data_complete=self._data_complete(context),
            factors=[
                FactorScore(**item)
                for item in factor_breakdown(scored_row, self._config)
            ],
        )
        return self._envelope(detail, context)

    # ---- 组装 ----

    def _build_opportunity(self, row: pd.Series, rank: int) -> RadarOpportunity:
        plan = build_trade_plan(row)
        return RadarOpportunity(
            rank=rank,
            ts_code=str(row.get("ts_code")),
            name=str(row.get("name") or row.get("ts_code")),
            industry=_optional_str(row.get("industry")),
            close=round(float(row.get("close") or 0.0), 2),
            pct_chg=round(float(row.get("pct_chg") or 0.0), 2),
            turnover_rate=_optional_round(row.get("turnover_rate"), 2),
            volume_ratio=_optional_round(row.get("volume_ratio"), 2),
            amount=round(float(row.get("amount") or 0.0) / 1e5, 2),
            score=round(float(row.get("score") or 0.0), 1),
            confidence=round(float(row.get("confidence") or 0.0), 1),
            signal=str(row.get("signal") or "NEUTRAL"),
            signal_label=str(row.get("signal_label") or "中性"),
            risk_level=str(row.get("risk_level") or "low"),
            bias=str(row.get("bias") or "neutral"),
            key_reasons=build_reasons(row),
            risk_flags=build_risk_flags(row),
            trade_plan=TradePlan(**plan),
        )

    def _envelope(self, data, context: RadarContext):
        expected = self._calendar.latest_trade_date()
        return Envelope(
            ok=True,
            source="sample" if context.origin == "sample" else "snapshot",
            as_of=datetime.now().astimezone().isoformat(timespec="seconds"),
            stale=context.trade_date < expected,
            warnings=_unique(context.warnings),
            data=data,
        )

    @staticmethod
    def _row_for(frame: pd.DataFrame, code: str) -> pd.Series | None:
        if frame is None or frame.empty:
            return None
        matched = frame[frame["ts_code"].astype(str).str.upper() == code]
        if matched.empty:
            return None
        return matched.iloc[0]

    def _write_snapshot(self, trade_date: str, items: list[RadarOpportunity]) -> None:
        path = (
            self._settings.data_dir
            / "snapshots"
            / "latest"
            / f"radar_top{len(items)}.json"
        )
        payload = {
            "trade_date": trade_date,
            "generated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
            "items": [item.model_dump(by_alias=True) for item in items],
        }
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(
                json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
            )
        except OSError as exc:  # 快照写入失败不应影响接口
            logger.warning("雷达快照写入失败：%s", exc)


def _optional_round(value, digits: int) -> float | None:
    if value is None:
        return None
    number = float(value)
    if np.isnan(number) or np.isinf(number):
        return None
    return round(number, digits)


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
