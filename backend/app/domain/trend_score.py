"""趋势评分规则（线上信号与回测共用的纯函数）。

从 SignalService 抽取，保证「回测信号 = 线上信号」同一套口径。
输入均为按日期升序的日线/周线 DataFrame，不读取存储、不依赖 FastAPI。
"""

from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np
import pandas as pd

from app.domain.indicators import boll as boll_indicator
from app.domain.indicators import macd as macd_indicator
from app.domain.indicators import rsi as rsi_indicator
from app.domain.indicators import sma

TREND_FACTORS: tuple[tuple[str, str, float], ...] = (
    ("ma", "均线排列", 0.30),
    ("macd", "MACD 动能", 0.20),
    ("rsi", "RSI 强弱", 0.15),
    ("boll", "布林位置", 0.15),
    ("volume", "量价配合", 0.10),
    ("relative_strength", "相对强度", 0.10),
)

WEEKLY_SKIP = {"relative_strength"}


@dataclass(frozen=True)
class Thresholds:
    """信号分档阈值，与 SignalConfig 保持同名字段。"""

    strong_buy: float = 60.0
    buy: float = 30.0
    watch: float = 10.0
    hold: float = -10.0
    reduce: float = -30.0


DEFAULT_THRESHOLDS = Thresholds()


def component_scores(
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
    components["rsi"] = rsi_component(rsi_value)

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

    components["volume"] = volume_component(bars)
    if rs is None:
        components["relative_strength"] = (0.0, "周线相对强度按中性处理")
    else:
        components["relative_strength"] = rs
    return components


def rsi_component(value: float) -> tuple[float, str]:
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


def volume_component(bars: pd.DataFrame) -> tuple[float, str]:
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


def weighted_score(
    components: dict[str, tuple[float, str]],
    *,
    skip: set[str] | frozenset[str] = frozenset(),
) -> tuple[float, list[dict]]:
    """返回 (总分, 分项明细)；明细字段与 ScorePart 对齐。"""
    effective = {
        key: weight for key, _, weight in TREND_FACTORS if key not in skip
    }
    total_weight = sum(effective.values()) or 1.0
    score = 0.0
    parts: list[dict] = []
    for key, label, weight in TREND_FACTORS:
        if key not in effective:
            continue
        raw, reason = components.get(key, (0.0, "缺少数据"))
        contribution = 100.0 * weight / total_weight * raw
        score += contribution
        parts.append(
            {
                "factor": key,
                "label": label,
                "weight": round(weight / total_weight, 3),
                "raw": round(raw, 3),
                "contribution": round(contribution, 2),
                "reason": reason,
            }
        )
    return round(score, 2), parts


def signal_for(
    score: float, thresholds: Thresholds = DEFAULT_THRESHOLDS
) -> str:
    if score >= thresholds.strong_buy:
        return "STRONG_BUY"
    if score >= thresholds.buy:
        return "BUY"
    if score >= thresholds.watch:
        return "WATCH"
    if score > thresholds.hold:
        return "HOLD"
    if score > thresholds.reduce:
        return "REDUCE"
    return "SELL"


def direction(score: float) -> str:
    if score >= 10:
        return "bullish"
    if score <= -10:
        return "bearish"
    return "neutral"


def confidence(score: float, data_complete: bool) -> float:
    value = 35.0 + 0.45 * abs(score) + (10.0 if data_complete else 0.0)
    return float(np.clip(value, 25.0, 95.0))


def relative_strength_map(returns: dict[str, float]) -> dict[str, tuple[float, float]]:
    """把 20 日收益映射为 (-1~1, 百分位)。"""
    if not returns:
        return {}
    series = pd.Series(returns)
    percentile = series.rank(pct=True, method="average")
    return {
        code: (round(2.0 * float(percentile[code]) - 1.0, 4), float(percentile[code]) * 100.0)
        for code in returns
    }


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
    if math.isnan(number) or math.isinf(number):
        return 0.0
    return number
