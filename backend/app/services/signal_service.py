"""交易信号服务：趋势评分、多周期共振、交易计划与证据链。

对应 DESIGN.md 8.9 / 9.4 / 10.4。所有评分基于复权后的日线/周线，
信号在收盘后生成，输出中明确 A 股交易约束。
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import datetime
from typing import Iterable

import numpy as np
import pandas as pd

from app.core.cache import TTLCache
from app.core.config import Settings
from app.core.errors import AppError, DataNotReadyError, InsufficientHistoryError, InvalidParamError, SymbolNotFoundError
from app.data.calendar import TradeCalendar
from app.data.frames import coerce_numeric
from app.data.repository import Repository
from app.data.universe import limit_threshold
from app.domain.indicators import atr as atr_indicator
from app.domain.indicators import boll as boll_indicator
from app.domain.indicators import macd as macd_indicator
from app.domain.indicators import rsi as rsi_indicator
from app.domain.indicators import sma
from app.models.schemas import (
    Envelope,
    EvidenceGroup,
    ScorePart,
    SignalBatch,
    SignalBatchItem,
    SignalEvidence,
    SignalTradePlan,
    StockSignal,
)
from app.services.kline_service import KlineContext, KlineService, resample_bars

SIGNAL_LABELS = {
    "STRONG_BUY": "强买 · 日周共振",
    "BUY": "偏多",
    "WATCH": "观察",
    "HOLD": "中性",
    "REDUCE": "偏空",
    "SELL": "卖出",
}

TREND_FACTORS: tuple[tuple[str, str, float], ...] = (
    ("ma", "均线排列", 0.30),
    ("macd", "MACD 动能", 0.20),
    ("rsi", "RSI 强弱", 0.15),
    ("boll", "布林位置", 0.15),
    ("volume", "量价配合", 0.10),
    ("relative_strength", "相对强度", 0.10),
)

CONSTRAINTS = [
    "T+1：当日买入最早下一交易日卖出",
    "涨停不可买入、跌停不可卖出",
    "委托数量按 100 股整手",
    "费用：佣金万 2.5（最低 5 元）、印花税卖出 0.05%、过户费 0.001%",
    "信号基于收盘数据生成，最早下一交易日开盘执行，不以当日收盘价成交",
]

WEEKLY_SKIP = {"relative_strength"}


@dataclass(frozen=True)
class SignalConfig:
    """信号参数，默认值与 config/signal.yaml 保持一致。"""

    weights: dict[str, float] = field(
        default_factory=lambda: {key: weight for key, _, weight in TREND_FACTORS}
    )
    timeframes: dict[str, float] = field(
        default_factory=lambda: {"daily": 0.70, "weekly": 0.30}
    )
    strong_buy: float = 60.0
    buy: float = 30.0
    watch: float = 10.0
    hold: float = -10.0
    reduce: float = -30.0
    atr_stop_multiple: float = 1.5
    target1_atr: float = 2.0
    target2_atr: float = 3.0
    min_rr: float = 1.5
    risk_per_trade: float = 0.01
    max_position: float = 0.20
    capital: float = 1_000_000.0


DEFAULT_CONFIG = SignalConfig()


class SignalService:
    def __init__(
        self,
        settings: Settings,
        repository: Repository,
        calendar: TradeCalendar,
        kline: KlineService,
        config: SignalConfig | None = None,
    ) -> None:
        self._settings = settings
        self._repository = repository
        self._calendar = calendar
        self._kline = kline
        self._config = config or DEFAULT_CONFIG
        self._cache = TTLCache(maxsize=128)

    # ---- 对外接口 ----

    def get_signal(
        self, ts_code: str, date: str | None = None, period: str = "daily"
    ) -> Envelope[StockSignal]:
        if period != "daily":
            raise InvalidParamError("当前仅支持日线信号（period=daily）")

        context = self._kline.load_context(date)
        code = KlineService.resolve_code(ts_code, context.stock_basic)
        cache_key = ("signal", code, context.trade_date)
        cached: StockSignal | None = self._cache.get(cache_key)
        if cached is not None:
            return self._envelope(cached, context)

        adjusted = self._kline.adjusted_history(context)
        code_frame = adjusted[adjusted["ts_code"].astype(str).str.upper() == code]
        if code_frame.empty:
            raise SymbolNotFoundError(f"{code} 在 {context.trade_date} 没有可用数据")

        daily = code_frame.sort_values("trade_date").reset_index(drop=True)
        if len(daily) < 60:
            raise InsufficientHistoryError(
                f"{code} 仅有 {len(daily)} 根日线，至少需要 60 根才能生成信号",
                detail={"bars": len(daily), "required": 60},
            )
        weekly = resample_bars(daily, "weekly")
        if weekly.empty:
            raise InsufficientHistoryError(f"{code} 无法聚合周线")

        rs_score, rs_percentile = self._rs_map(context).get(code, (0.0, 50.0))
        rs_reason = (
            f"20 日相对强度处于横截面 {rs_percentile:.0f}% 分位"
            if rs_percentile != 50.0 or rs_score != 0.0
            else "相对强度数据不足，按中性处理"
        )

        daily_components = self._component_scores(daily, (rs_score, rs_reason))
        weekly_components = self._component_scores(weekly, None)
        daily_score, breakdown = self._weighted_score(daily_components, skip=set())
        weekly_score, _ = self._weighted_score(weekly_components, skip=WEEKLY_SKIP)

        weights = self._config.timeframes
        total = round(
            weights["daily"] * daily_score + weights["weekly"] * weekly_score, 2
        )
        signal, signal_label = self._signal_for(total)
        score = round(total, 2)

        name = self._name_of(code, context)
        plan = self._build_plan(
            daily,
            code=code,
            name=name,
            score=score,
            signal=signal,
        )
        evidence, data_warnings = self._evidence(
            daily, context, code, daily_score, breakdown, rs_reason
        )
        risk_flags = self._risk_flags(daily, plan, signal)

        warnings = _unique(list(context.warnings) + data_warnings)
        data = StockSignal(
            ts_code=code,
            name=name,
            trade_date=context.trade_date,
            period=period,
            close=round(float(daily["close"].iloc[-1]), 2),
            pct_chg=round(_number(daily["pct_chg"].iloc[-1]), 2),
            score=score,
            signal=signal,
            signal_label=signal_label,
            confidence=round(self._confidence(score, not data_warnings), 1),
            direction=self._direction(score),
            market_state=self._market_state(daily, daily_score),
            daily_score=daily_score,
            weekly_score=weekly_score,
            trade_plan=plan,
            score_breakdown=breakdown,
            evidence=evidence,
            risk_flags=risk_flags,
            warnings=warnings,
            constraints=CONSTRAINTS,
        )
        self._cache.set(cache_key, data, 600)
        return self._envelope(data, context)

    def get_batch(
        self,
        codes: str | Iterable[str] | None = None,
        *,
        date: str | None = None,
        limit: int = 20,
    ) -> Envelope[SignalBatch]:
        context = self._kline.load_context(date)
        limit = max(1, min(int(limit), 50))

        if codes is None or (isinstance(codes, str) and not codes.strip()):
            selected = self._top_by_amount(context, limit)
        elif isinstance(codes, str):
            selected = [item.strip().upper() for item in codes.split(",") if item.strip()]
        else:
            selected = [str(item).strip().upper() for item in codes if str(item).strip()]
        selected = selected[:limit]

        items: list[SignalBatchItem] = []
        for code in selected:
            try:
                signal = self.get_signal(code, date=context.trade_date).data
                items.append(
                    SignalBatchItem(
                        ts_code=signal.ts_code,
                        name=signal.name,
                        close=signal.close,
                        pct_chg=signal.pct_chg,
                        score=signal.score,
                        signal=signal.signal,
                        signal_label=signal.signal_label,
                        confidence=signal.confidence,
                        direction=signal.direction,
                        entry_low=signal.trade_plan.entry_low,
                        stop_loss=signal.trade_plan.stop_loss,
                        target1=signal.trade_plan.target1,
                        executable=signal.trade_plan.executable,
                    )
                )
            except AppError as exc:
                items.append(
                    SignalBatchItem(
                        ts_code=code,
                        name=code,
                        close=0.0,
                        pct_chg=0.0,
                        score=0.0,
                        signal="HOLD",
                        signal_label=SIGNAL_LABELS["HOLD"],
                        confidence=0.0,
                        direction="neutral",
                        entry_low=0.0,
                        stop_loss=0.0,
                        target1=0.0,
                        executable=False,
                        error=exc.message,
                    )
                )

        data = SignalBatch(trade_date=context.trade_date, items=items)
        return self._envelope(data, context)

    # ---- 评分 ----

    def _component_scores(
        self,
        bars: pd.DataFrame,
        rs: tuple[float, str] | None,
    ) -> dict[str, tuple[float, str]]:
        closes = bars["close"].astype(float)
        close = float(closes.iloc[-1])
        components: dict[str, tuple[float, str]] = {}

        ma5, ma10 = _last(sma(closes, 5)), _last(sma(closes, 10))
        ma20, ma60 = _last(sma(closes, 20)), _last(sma(closes, 60))
        if any(math.isnan(value) for value in (ma5, ma10, ma20, ma60)):
            components["ma"] = (0.0, "均线数据不足，按中性处理")
        elif close > ma5 > ma10 > ma20 > ma60:
            components["ma"] = (1.0, "均线多头排列")
        elif close > ma20 and close > ma60:
            components["ma"] = (0.6, "收盘价站上 MA20 与 MA60")
        elif close < ma20 and close < ma60:
            components["ma"] = (-1.0, "收盘价跌破 MA20 与 MA60")
        else:
            components["ma"] = (0.0, "均线缠绕，方向不明")

        macd_frame = macd_indicator(closes)
        hist_now = _last(macd_frame["hist"])
        hist_prev = _last(macd_frame["hist"].shift(1))
        if math.isnan(hist_now) or math.isnan(hist_prev):
            components["macd"] = (0.0, "MACD 数据不足")
        elif hist_now > 0 and hist_now >= hist_prev:
            components["macd"] = (1.0, "MACD 红柱放大")
        elif hist_now > 0:
            components["macd"] = (0.3, "MACD 红柱缩短")
        elif hist_now < 0 and hist_now < hist_prev:
            components["macd"] = (-1.0, "MACD 绿柱放大")
        else:
            components["macd"] = (-0.3, "MACD 绿柱缩短")

        rsi_value = _last(rsi_indicator(closes))
        components["rsi"] = self._rsi_component(rsi_value)

        boll_frame = boll_indicator(closes)
        upper = _last(boll_frame["upper"])
        middle = _last(boll_frame["middle"])
        lower = _last(boll_frame["lower"])
        if any(math.isnan(value) for value in (upper, middle, lower)):
            components["boll"] = (0.0, "布林带数据不足")
        elif close >= upper * 0.999:
            components["boll"] = (0.5, "触及布林上轨")
        elif close <= lower * 1.001:
            components["boll"] = (-0.5, "触及布林下轨")
        elif close > middle:
            components["boll"] = (0.2, "运行于布林中轨上方")
        else:
            components["boll"] = (-0.2, "运行于布林中轨下方")

        components["volume"] = self._volume_component(bars)
        if rs is None:
            components["relative_strength"] = (0.0, "周线相对强度按中性处理")
        else:
            components["relative_strength"] = rs
        return components

    @staticmethod
    def _rsi_component(value: float) -> tuple[float, str]:
        if math.isnan(value):
            return 0.0, "RSI 数据不足"
        if 50 <= value < 70:
            return 0.6, f"RSI {value:.1f}，处于强势区"
        if 40 <= value < 50:
            return 0.2, f"RSI {value:.1f}，动能温和"
        if 70 <= value <= 80:
            return 0.1, f"RSI {value:.1f}，接近超买"
        if value > 80:
            return -0.6, f"RSI {value:.1f}，超买风险"
        if 30 <= value < 40:
            return -0.2, f"RSI {value:.1f}，动能偏弱"
        return -0.8, f"RSI {value:.1f}，超卖区"

    @staticmethod
    def _volume_component(bars: pd.DataFrame) -> tuple[float, str]:
        volumes = bars["vol"].astype(float)
        volume = _last(volumes)
        base = _last(sma(volumes, 5))
        pct = _number(bars["pct_chg"].iloc[-1])
        if math.isnan(volume) or math.isnan(base) or base <= 0:
            return 0.0, "量能数据不足"
        ratio = volume / base
        if pct > 0 and ratio >= 1.2:
            return 1.0, f"放量上涨，量能 {ratio:.2f} 倍"
        if pct > 0 and ratio < 0.8:
            return 0.3, f"缩量上涨，量能 {ratio:.2f} 倍"
        if pct < 0 and ratio >= 1.2:
            return -1.0, f"放量下跌，量能 {ratio:.2f} 倍"
        if pct < 0 and ratio < 0.8:
            return -0.3, f"缩量下跌，量能 {ratio:.2f} 倍"
        return 0.0, f"量能平稳，量比 {ratio:.2f}"

    def _weighted_score(
        self,
        components: dict[str, tuple[float, str]],
        *,
        skip: set[str],
    ) -> tuple[float, list[ScorePart]]:
        effective = {
            key: weight
            for key, _, weight in TREND_FACTORS
            if key not in skip
        }
        total_weight = sum(effective.values()) or 1.0
        score = 0.0
        parts: list[ScorePart] = []
        for key, label, weight in TREND_FACTORS:
            if key not in effective:
                continue
            raw, reason = components.get(key, (0.0, "缺少数据"))
            contribution = 100.0 * weight / total_weight * raw
            score += contribution
            parts.append(
                ScorePart(
                    factor=key,
                    label=label,
                    weight=round(weight / total_weight, 3),
                    raw=round(raw, 3),
                    contribution=round(contribution, 2),
                    reason=reason,
                )
            )
        return round(score, 2), parts

    def _signal_for(self, score: float) -> tuple[str, str]:
        config = self._config
        if score >= config.strong_buy:
            key = "STRONG_BUY"
        elif score >= config.buy:
            key = "BUY"
        elif score >= config.watch:
            key = "WATCH"
        elif score > config.hold:
            key = "HOLD"
        elif score > config.reduce:
            key = "REDUCE"
        else:
            key = "SELL"
        return key, SIGNAL_LABELS[key]

    @staticmethod
    def _direction(score: float) -> str:
        if score >= 10:
            return "bullish"
        if score <= -10:
            return "bearish"
        return "neutral"

    @staticmethod
    def _confidence(score: float, data_complete: bool) -> float:
        value = 35.0 + 0.45 * abs(score) + (10.0 if data_complete else 0.0)
        return float(np.clip(value, 25.0, 95.0))

    @staticmethod
    def _market_state(daily: pd.DataFrame, daily_score: float) -> str:
        atr_value = _last(atr_indicator(daily))
        close = _number(daily["close"].iloc[-1])
        if close > 0 and not math.isnan(atr_value) and atr_value / close > 0.05:
            return "volatile"
        if abs(daily_score) >= 30:
            return "trending"
        return "ranging"

    # ---- 相对强度 ----

    def _rs_map(self, context: KlineContext) -> dict[str, tuple[float, float]]:
        key = ("rs20", context.trade_date)
        cached = self._cache.get(key)
        if cached is not None:
            return cached

        adjusted = self._kline.adjusted_history(context)
        values: dict[str, float] = {}
        for code, group in adjusted.groupby("ts_code", sort=False):
            closes = group["close"].to_numpy(dtype="float64")
            if closes.size < 21 or closes[-21] <= 0 or closes[-1] <= 0:
                continue
            values[str(code).upper()] = float(closes[-1] / closes[-21] - 1.0)

        if not values:
            mapping: dict[str, tuple[float, float]] = {}
        else:
            series = pd.Series(values)
            percentile = series.rank(pct=True, method="average")
            mapping = {
                code: (round(2.0 * float(percentile[code]) - 1.0, 4), float(percentile[code]) * 100.0)
                for code in values
            }
        self._cache.set(key, mapping, 600)
        return mapping

    # ---- 交易计划 ----

    def _build_plan(
        self,
        daily: pd.DataFrame,
        *,
        code: str,
        name: str,
        score: float,
        signal: str,
    ) -> SignalTradePlan:
        config = self._config
        closes = daily["close"].astype(float)
        highs = daily["high"].astype(float)
        lows = daily["low"].astype(float)
        close = float(closes.iloc[-1])
        atr_value = _last(atr_indicator(daily))
        if math.isnan(atr_value) or atr_value <= 0:
            atr_value = close * 0.02
        ma20 = _last(sma(closes, 20))
        if math.isnan(ma20) or ma20 <= 0:
            ma20 = close

        high20 = float(highs.tail(20).max())
        low20 = float(lows.tail(20).min())
        breakout = close >= high20 * 0.995
        if breakout:
            entry_low = max(close, high20)
            entry_high = entry_low * 1.01
            entry_type = "breakout"
        else:
            entry_low = ma20 * 0.99
            entry_high = ma20 * 1.01
            entry_type = "pullback"
        entry = (entry_low + entry_high) / 2.0

        stops = [entry_low - config.atr_stop_multiple * atr_value, ma20 * 0.97, low20 * 0.99]
        stop = min(stops)
        stop = min(stop, entry_low * 0.999)
        stop = max(stop, entry * 0.85)
        risk_per_share = entry - stop

        resistance = high20
        target1 = (
            min(resistance, entry + config.target1_atr * atr_value)
            if resistance > entry * 1.005
            else entry + config.target1_atr * atr_value
        )
        if target1 <= entry:
            target1 = entry + config.target1_atr * atr_value
        target2 = entry + config.target2_atr * atr_value

        rr1 = (target1 - entry) / risk_per_share if risk_per_share > 0 else 0.0
        rr2 = (target2 - entry) / risk_per_share if risk_per_share > 0 else 0.0

        risk_budget = config.capital * config.risk_per_trade
        shares = 0
        if risk_per_share > 0:
            shares = int(math.floor(risk_budget / risk_per_share / 100.0) * 100)
        max_shares = int(
            math.floor(config.capital * config.max_position / entry / 100.0) * 100
        )
        shares = max(0, min(shares, max_shares))
        position_pct = shares * entry / config.capital * 100.0

        pct_chg = _number(daily["pct_chg"].iloc[-1])
        limit_pct = limit_threshold(code, name) * 100.0
        limit_up = pct_chg >= limit_pct - 0.2
        limit_down = pct_chg <= -limit_pct + 0.2
        volume = _number(daily["vol"].iloc[-1])
        executable = (
            not limit_up
            and not limit_down
            and volume > 0
            and rr1 >= config.min_rr
            and score >= config.watch
        )

        notes = ["信号于收盘后生成，最早下一交易日执行"]
        if limit_up:
            notes.append("当日涨停，不可追高")
        if limit_down:
            notes.append("当日跌停，不可卖出")
        if volume <= 0:
            notes.append("当日停牌或无成交")
        if rr1 < config.min_rr:
            notes.append(f"盈亏比 {rr1:.2f} 低于 {config.min_rr:.1f}")
        if signal in {"HOLD", "REDUCE", "SELL"}:
            notes.append("当前信号不建议新开仓")

        return SignalTradePlan(
            entry_low=round(entry_low, 2),
            entry_high=round(entry_high, 2),
            stop_loss=round(stop, 2),
            target1=round(target1, 2),
            target2=round(target2, 2),
            rr1=round(rr1, 2),
            rr2=round(rr2, 2),
            position_pct=round(position_pct, 1),
            entry_type=entry_type,
            executable=bool(executable),
            execution_note="；".join(notes),
            shares=int(shares),
        )

    # ---- 证据链 ----

    def _evidence(
        self,
        daily: pd.DataFrame,
        context: KlineContext,
        code: str,
        daily_score: float,
        breakdown: list[ScorePart],
        rs_reason: str,
    ) -> tuple[SignalEvidence, list[str]]:
        warnings: list[str] = []
        technical_score = float(np.clip(50.0 + daily_score / 2.0, 0.0, 100.0))
        technical_reasons = [
            part.reason for part in breakdown if abs(part.raw) >= 0.2
        ][:4] or [rs_reason]

        moneyflow = self._latest_dataset("moneyflow", context.trade_date)
        amount = _number(daily["amount"].iloc[-1])
        if moneyflow.empty or amount <= 0:
            capital_score = 50.0
            capital_reasons = ["缺少资金流数据，按中性处理"]
            warnings.append("缺少 moneyflow，资金流证据按中性处理")
        else:
            row = moneyflow[moneyflow["ts_code"].astype(str).str.upper() == code]
            if row.empty:
                capital_score = 50.0
                capital_reasons = ["该标的当日无资金流数据"]
            else:
                net = _number(row.iloc[0].get("net_mf_amount"))
                ratio = net * 10.0 / amount
                capital_score = float(np.clip(50.0 + ratio * 250.0, 0.0, 100.0))
                capital_reasons = [f"主力净流入占成交额 {ratio * 100:+.1f}%"]

        pct_chg = _number(daily["pct_chg"].iloc[-1])
        volume_ratio = self._volume_ratio(daily, context.trade_date, code)
        sentiment_score = float(
            np.clip(50.0 + (volume_ratio - 1.0) * 25.0 + pct_chg * 2.0, 0.0, 100.0)
        )
        sentiment_reasons = [
            f"量比 {volume_ratio:.2f}",
            f"当日涨跌幅 {pct_chg:+.2f}%",
        ]

        evidence = SignalEvidence(
            technical=EvidenceGroup(
                bias=_bias_for(technical_score),
                score=round(technical_score, 1),
                reasons=technical_reasons,
            ),
            capital=EvidenceGroup(
                bias=_bias_for(capital_score),
                score=round(capital_score, 1),
                reasons=capital_reasons,
            ),
            sentiment=EvidenceGroup(
                bias=_bias_for(sentiment_score),
                score=round(sentiment_score, 1),
                reasons=sentiment_reasons,
            ),
        )
        return evidence, warnings

    def _volume_ratio(self, daily: pd.DataFrame, trade_date: str, code: str) -> float:
        basic = self._latest_dataset("daily_basic", trade_date)
        if not basic.empty and "volume_ratio" in basic.columns:
            row = basic[basic["ts_code"].astype(str).str.upper() == code]
            if not row.empty:
                value = _number(row.iloc[0].get("volume_ratio"))
                if value > 0:
                    return value
        volumes = daily["vol"].astype(float)
        base = _last(sma(volumes, 5))
        volume = _last(volumes)
        if math.isnan(base) or base <= 0 or math.isnan(volume):
            return 1.0
        return float(volume / base)

    def _risk_flags(
        self,
        daily: pd.DataFrame,
        plan: SignalTradePlan,
        signal: str,
    ) -> list[str]:
        flags: list[str] = []
        highs = daily["high"].astype(float)
        lows = daily["low"].astype(float)
        closes = daily["close"].astype(float)
        close = float(closes.iloc[-1])
        high60 = float(highs.tail(60).max())
        low60 = float(lows.tail(60).min())
        if close >= high60 * 0.98:
            flags.append("接近 60 日高点")
        if close <= low60 * 1.02:
            flags.append("接近 60 日低点")

        atr_value = _last(atr_indicator(daily))
        if not math.isnan(atr_value) and close > 0 and atr_value / close > 0.05:
            flags.append(f"波动率偏高（ATR {atr_value / close * 100:.1f}%）")

        rsi_value = _last(rsi_indicator(closes))
        if not math.isnan(rsi_value):
            if rsi_value > 80:
                flags.append(f"RSI {rsi_value:.0f} 超买")
            elif rsi_value < 20:
                flags.append(f"RSI {rsi_value:.0f} 超卖")

        pct_chg = _number(daily["pct_chg"].iloc[-1])
        if pct_chg >= 9.8:
            flags.append("当日涨停")
        elif pct_chg <= -9.8:
            flags.append("当日跌停")

        if not plan.executable and signal in {"STRONG_BUY", "BUY", "WATCH"}:
            flags.append("交易计划暂不可执行")
        return flags

    # ---- 辅助 ----

    def _latest_dataset(self, dataset: str, trade_date: str) -> pd.DataFrame:
        key = ("dataset", dataset, trade_date)
        cached = self._cache.get(key)
        if cached is not None:
            return cached
        loaded = self._repository.read_dataset(dataset, trade_date)
        frame = coerce_numeric(loaded.frame) if loaded is not None else pd.DataFrame()
        self._cache.set(key, frame, 600)
        return frame

    def _top_by_amount(self, context: KlineContext, limit: int) -> list[str]:
        latest = context.history[context.history["trade_date"].astype(str) == context.trade_date]
        if latest.empty:
            raise DataNotReadyError(f"{context.trade_date} 缺少可用横截面数据")
        top = latest.sort_values("amount", ascending=False).head(limit)
        return [str(code).upper() for code in top["ts_code"]]

    @staticmethod
    def _name_of(code: str, context: KlineContext) -> str:
        stock_basic = context.stock_basic
        if stock_basic.empty or "name" not in stock_basic.columns:
            return code
        matched = stock_basic[stock_basic["ts_code"].astype(str).str.upper() == code]
        if matched.empty:
            return code
        name = matched.iloc[0].get("name")
        return str(name) if name else code

    def _envelope(self, data, context: KlineContext):
        expected = self._calendar.latest_trade_date()
        return Envelope(
            ok=True,
            source="sample" if context.origin == "sample" else "snapshot",
            as_of=datetime.now().astimezone().isoformat(timespec="seconds"),
            stale=context.trade_date < expected,
            warnings=_unique(context.warnings),
            data=data,
        )


def _last(series: pd.Series) -> float:
    if series is None or len(series) == 0:
        return float("nan")
    value = series.iloc[-1]
    return _number(value) if not pd.isna(value) else float("nan")


def _number(value) -> float:
    if value is None:
        return 0.0
    try:
        number = float(value)
    except (TypeError, ValueError):
        return 0.0
    if np.isnan(number) or np.isinf(number):
        return 0.0
    return number


def _bias_for(score: float) -> str:
    if score >= 60:
        return "bullish"
    if score <= 40:
        return "bearish"
    return "neutral"


def _unique(items: Iterable[str]) -> list[str]:
    seen: set[str] = set()
    output: list[str] = []
    for item in items:
        if item not in seen:
            seen.add(item)
            output.append(item)
    return output
