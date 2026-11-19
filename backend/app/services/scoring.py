"""机会雷达评分算法（DESIGN.md 10.2）。

流程：
1. 由个股历史日线计算原始因子（动量、量比、趋势、波动等）；
2. 对每个因子做横截面百分位（0-100）；
3. 按权重加权得到基础分，再扣减风险项；
4. 映射信号等级、置信度、理由与风险标记。

本模块只做纯计算，不读取数据、不依赖 FastAPI。
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
import pandas as pd

from app.data.universe import limit_threshold

# 因子 -> 特征列
FACTOR_COLUMNS: dict[str, str] = {
    "momentum_1d": "pct_chg",
    "momentum_5d": "mom_5d",
    "volume_ratio": "volume_ratio",
    "turnover": "turnover_rate",
    "trend": "trend_score",
    "capital_flow": "capital_ratio",
    "relative_strength": "rs_20",
}

FACTOR_LABELS: dict[str, str] = {
    "momentum_1d": "当日动量",
    "momentum_5d": "5 日动量",
    "volume_ratio": "量比",
    "turnover": "换手率",
    "trend": "趋势形态",
    "capital_flow": "资金流",
    "relative_strength": "相对强度",
}

DEFAULT_WEIGHTS: dict[str, float] = {
    "momentum_1d": 0.18,
    "momentum_5d": 0.14,
    "volume_ratio": 0.14,
    "turnover": 0.10,
    "trend": 0.18,
    "capital_flow": 0.14,
    "relative_strength": 0.12,
}

DEFAULT_RISK_PENALTY: dict[str, float] = {
    "limit_down": 30,
    "consecutive_limit_up_3": 20,
    "range_pos_gt_98": 10,
    "atr_pct_gt_8": 10,
}

SIGNAL_LABELS: dict[str, str] = {
    "STRONG_BUY": "多维共振",
    "BUY": "偏多",
    "WATCH": "观察",
    "NEUTRAL": "中性",
    "AVOID": "回避",
}


@dataclass(frozen=True)
class RadarConfig:
    """雷达评分参数，默认值与 config/radar.yaml 保持一致。"""

    weights: dict[str, float] = field(default_factory=lambda: dict(DEFAULT_WEIGHTS))
    penalties: dict[str, float] = field(default_factory=lambda: dict(DEFAULT_RISK_PENALTY))
    min_list_days: int = 60
    min_price: float = 2.0

    @property
    def weight_sum(self) -> float:
        return float(sum(self.weights.values())) or 1.0


DEFAULT_CONFIG = RadarConfig()


# ---------------------------------------------------------------------------
# 因子特征
# ---------------------------------------------------------------------------


def _mean(values: np.ndarray) -> float:
    if values.size == 0:
        return float("nan")
    return float(np.nanmean(values))


def _ma(closes: np.ndarray, window: int) -> float:
    if closes.size < window:
        return float("nan")
    window_values = closes[-window:]
    if np.isnan(window_values).any():
        return float("nan")
    return float(window_values.mean())


def _ma_score(close: float, ma5: float, ma10: float, ma20: float, ma60: float) -> float:
    values = (close, ma5, ma10, ma20, ma60)
    if any(np.isnan(value) for value in values):
        return 40.0
    if close > ma5 > ma10 > ma20 > ma60:
        return 100.0
    if close > ma20 > ma60:
        return 70.0
    if close > ma20:
        return 55.0
    if close < ma20 < ma60:
        return 10.0
    return 40.0


def _macd_hist(closes: np.ndarray) -> tuple[float, float]:
    series = pd.Series(closes, dtype="float64")
    ema12 = series.ewm(span=12, adjust=False).mean()
    ema26 = series.ewm(span=26, adjust=False).mean()
    dif = ema12 - ema26
    dea = dif.ewm(span=9, adjust=False).mean()
    hist = dif - dea
    if len(hist) == 0:
        return float("nan"), float("nan")
    current = float(hist.iloc[-1])
    previous = float(hist.iloc[-2]) if len(hist) >= 2 else current
    return current, previous


def _macd_score(hist: float, prev: float) -> float:
    if np.isnan(hist) or np.isnan(prev):
        return 40.0
    if hist > 0 and hist > prev:
        return 100.0
    if hist > 0:
        return 65.0
    if hist > prev:
        return 40.0
    return 10.0


def compute_stock_features(
    history: pd.DataFrame,
    *,
    stock_basic: pd.DataFrame | None = None,
    trading_age: dict[str, int] | None = None,
) -> pd.DataFrame:
    """对每只股票计算截至最后一根 K 线的因子与风险特征。"""

    if history is None or history.empty:
        return pd.DataFrame()

    frame = history.copy()
    for column in ("open", "high", "low", "close", "pre_close", "pct_chg", "vol", "amount"):
        if column in frame.columns:
            frame[column] = pd.to_numeric(frame[column], errors="coerce")
    frame = frame.dropna(subset=["close"]).sort_values(["ts_code", "trade_date"])

    names: dict[str, str] = {}
    industries: dict[str, str] = {}
    if stock_basic is not None and not stock_basic.empty:
        for _, row in stock_basic.iterrows():
            code = str(row.get("ts_code"))
            names[code] = str(row.get("name") or "")
            industries[code] = str(row.get("industry") or "")

    records: list[dict] = []
    for code, group in frame.groupby("ts_code", sort=False):
        if len(group) < 2:
            continue
        closes = group["close"].to_numpy(dtype="float64")
        highs = (
            group["high"].to_numpy(dtype="float64")
            if "high" in group.columns
            else closes
        )
        lows = group["low"].to_numpy(dtype="float64") if "low" in group.columns else closes
        vols = group["vol"].to_numpy(dtype="float64") if "vol" in group.columns else np.array([])
        amounts = (
            group["amount"].to_numpy(dtype="float64")
            if "amount" in group.columns
            else np.array([])
        )
        pct = (
            group["pct_chg"].to_numpy(dtype="float64")
            if "pct_chg" in group.columns
            else np.zeros(len(group))
        )
        pre_close = (
            group["pre_close"].to_numpy(dtype="float64")
            if "pre_close" in group.columns
            else np.full(len(group), np.nan)
        )

        close = float(closes[-1])
        ma5, ma10 = _ma(closes, 5), _ma(closes, 10)
        ma20, ma60 = _ma(closes, 20), _ma(closes, 60)
        ma_score = _ma_score(close, ma5, ma10, ma20, ma60)

        hist_now, hist_prev = _macd_hist(closes)
        macd_score = _macd_score(hist_now, hist_prev)
        trend_score = 0.6 * ma_score + 0.4 * macd_score

        mom_5d = close / closes[-6] - 1.0 if len(closes) >= 6 and closes[-6] > 0 else np.nan
        ret_20 = close / closes[-21] - 1.0 if len(closes) >= 21 and closes[-21] > 0 else np.nan

        volume_ratio_calc = np.nan
        if vols.size >= 6:
            base = _mean(vols[-6:-1])
            if base > 0 and not np.isnan(vols[-1]):
                volume_ratio_calc = float(vols[-1] / base)

        atr = np.nan
        if highs.size == lows.size == closes.size and closes.size >= 2:
            true_ranges = []
            for index in range(1, len(closes)):
                prev = pre_close[index]
                if np.isnan(prev):
                    prev = closes[index - 1]
                true_ranges.append(
                    max(
                        highs[index] - lows[index],
                        abs(highs[index] - prev),
                        abs(lows[index] - prev),
                    )
                )
            atr = _mean(np.array(true_ranges[-14:]))

        range_pos_20 = np.nan
        if highs.size >= 20 and lows.size >= 20:
            hi20 = float(np.nanmax(highs[-20:]))
            lo20 = float(np.nanmin(lows[-20:]))
            if hi20 > lo20:
                range_pos_20 = (close - lo20) / (hi20 - lo20)

        name = names.get(code, "")
        limit_pct = limit_threshold(code, name) * 100.0
        limit_up_streak = 0
        for value in pct[::-1]:
            if np.isnan(value) or value < limit_pct - 0.2:
                break
            limit_up_streak += 1
        is_limit_down = bool(pct[-1] <= -limit_pct + 0.2) if not np.isnan(pct[-1]) else False
        is_limit_up = bool(pct[-1] >= limit_pct - 0.2) if not np.isnan(pct[-1]) else False

        records.append(
            {
                "ts_code": code,
                "name": name or code,
                "industry": industries.get(code) or None,
                "close": close,
                "pct_chg": float(pct[-1]) if not np.isnan(pct[-1]) else 0.0,
                "amount": float(amounts[-1]) if amounts.size else 0.0,
                "vol": float(vols[-1]) if vols.size else 0.0,
                "mom_5d": mom_5d,
                "ret_20": ret_20,
                "volume_ratio_calc": volume_ratio_calc,
                "ma5": ma5,
                "ma10": ma10,
                "ma20": ma20,
                "ma60": ma60,
                "ma_score": ma_score,
                "macd_hist": hist_now,
                "macd_score": macd_score,
                "trend_score": trend_score,
                "atr": atr,
                "atr_pct": (atr / close) if close > 0 and not np.isnan(atr) else np.nan,
                "range_pos_20": range_pos_20,
                "avg_amount_20": _mean(amounts[-20:]) if amounts.size else np.nan,
                "limit_up_streak": limit_up_streak,
                "is_limit_up": is_limit_up,
                "is_limit_down": is_limit_down,
                "list_days": int((trading_age or {}).get(code, 0)),
                "history_days": int(len(closes)),
            }
        )

    return pd.DataFrame.from_records(records)


# ---------------------------------------------------------------------------
# 横截面评分
# ---------------------------------------------------------------------------


def signal_for(score: float) -> tuple[str, str]:
    if score >= 80:
        key = "STRONG_BUY"
    elif score >= 65:
        key = "BUY"
    elif score >= 50:
        key = "WATCH"
    elif score >= 35:
        key = "NEUTRAL"
    else:
        key = "AVOID"
    return key, SIGNAL_LABELS[key]


def confidence_for(score: float, data_complete: bool) -> float:
    bonus = 10.0 if data_complete else 0.0
    value = 40.0 + 0.4 * abs(score - 50.0) + bonus
    return float(np.clip(value, 25.0, 95.0))


def bias_for(score: float) -> str:
    if score >= 65:
        return "bullish"
    if score < 35:
        return "bearish"
    return "neutral"


def risk_level_for(penalty: float) -> str:
    if penalty >= 20:
        return "high"
    if penalty >= 10:
        return "medium"
    return "low"


def score_cross_section(
    features: pd.DataFrame,
    *,
    config: RadarConfig | None = None,
    data_complete: bool = True,
) -> pd.DataFrame:
    """在给定横截面上做百分位打分，返回带评分列的新 DataFrame。"""

    if features is None or features.empty:
        return pd.DataFrame() if features is None else features.copy()

    config = config or DEFAULT_CONFIG
    out = features.copy()
    weighted = pd.Series(0.0, index=out.index)

    for key, weight in config.weights.items():
        column = FACTOR_COLUMNS[key]
        if column in out.columns:
            values = pd.to_numeric(out[column], errors="coerce")
        else:
            values = pd.Series(np.nan, index=out.index)
        percentile = values.rank(pct=True, method="average") * 100.0
        percentile = percentile.fillna(50.0)
        out[f"pct_{key}"] = percentile.round(2)
        weighted = weighted + (weight / config.weight_sum) * percentile

    out["raw_score"] = weighted.round(2)

    penalty = pd.Series(0.0, index=out.index)
    if "limit_down" in config.penalties:
        penalty += np.where(
            out.get("is_limit_down", pd.Series(False, index=out.index)).fillna(False),
            config.penalties["limit_down"],
            0.0,
        )
    if "consecutive_limit_up_3" in config.penalties:
        penalty += np.where(
            out.get("limit_up_streak", pd.Series(0, index=out.index)).fillna(0) >= 3,
            config.penalties["consecutive_limit_up_3"],
            0.0,
        )
    if "range_pos_gt_98" in config.penalties:
        penalty += np.where(
            out.get("range_pos_20", pd.Series(0.0, index=out.index)).fillna(0) > 0.98,
            config.penalties["range_pos_gt_98"],
            0.0,
        )
    if "atr_pct_gt_8" in config.penalties:
        penalty += np.where(
            out.get("atr_pct", pd.Series(0.0, index=out.index)).fillna(0) > 0.08,
            config.penalties["atr_pct_gt_8"],
            0.0,
        )

    out["risk_penalty"] = penalty.round(2)
    out["score"] = (out["raw_score"] - out["risk_penalty"]).clip(0, 100).round(2)

    signals = [signal_for(float(score)) for score in out["score"]]
    out["signal"] = [item[0] for item in signals]
    out["signal_label"] = [item[1] for item in signals]
    out["bias"] = out["score"].map(bias_for)
    out["risk_level"] = out["risk_penalty"].map(risk_level_for)
    out["confidence"] = [
        round(confidence_for(float(score), data_complete), 1) for score in out["score"]
    ]
    return out


# ---------------------------------------------------------------------------
# 解释性输出
# ---------------------------------------------------------------------------


def build_reasons(row: pd.Series, limit: int = 4) -> list[str]:
    reasons: list[str] = []
    ma_score = float(row.get("ma_score") or 0)
    ma20, ma60 = row.get("ma20"), row.get("ma60")
    if ma_score >= 70 and not pd.isna(ma20) and not pd.isna(ma60):
        reasons.append(f"收盘价站上 MA20（{ma20:.2f}）与 MA60（{ma60:.2f}）")
    elif ma_score >= 55 and not pd.isna(ma20):
        reasons.append(f"收盘价站上 MA20（{ma20:.2f}）")

    macd_hist = row.get("macd_hist")
    if macd_hist is not None and not pd.isna(macd_hist) and macd_hist > 0:
        reasons.append("MACD 位于零轴上方，柱线保持强势")

    volume_ratio = row.get("volume_ratio")
    if volume_ratio is not None and not pd.isna(volume_ratio) and volume_ratio >= 1.2:
        reasons.append(f"量比 {volume_ratio:.2f}，成交较 5 日均值放大")

    capital_ratio = row.get("capital_ratio")
    if capital_ratio is not None and not pd.isna(capital_ratio) and capital_ratio > 0.01:
        reasons.append(f"主力净流入占成交额 {capital_ratio * 100:.1f}%")

    mom_5d = row.get("mom_5d")
    if mom_5d is not None and not pd.isna(mom_5d) and mom_5d > 0.03:
        reasons.append(f"5 日累计涨幅 {mom_5d * 100:.1f}%")

    rs_20 = row.get("rs_20")
    if rs_20 is not None and not pd.isna(rs_20) and rs_20 > 0.02:
        reasons.append(f"20 日跑赢沪深300 {rs_20 * 100:.1f} 个百分点")

    if not reasons:
        reasons.append("因子表现中性，暂无明显优势")
    return reasons[:limit]


def build_risk_flags(row: pd.Series) -> list[str]:
    flags: list[str] = []
    streak = int(row.get("limit_up_streak") or 0)
    if streak >= 2:
        flags.append(f"连续涨停 {streak} 天")
    elif streak == 1:
        flags.append("今日涨停")

    if bool(row.get("is_limit_down")):
        flags.append("今日跌停")

    range_pos = row.get("range_pos_20")
    if range_pos is not None and not pd.isna(range_pos) and range_pos > 0.98:
        flags.append("接近 20 日高点")

    atr_pct = row.get("atr_pct")
    if atr_pct is not None and not pd.isna(atr_pct) and atr_pct > 0.08:
        flags.append(f"波动率偏高（ATR {atr_pct * 100:.1f}%）")

    ma20 = row.get("ma20")
    close = row.get("close")
    if ma20 is not None and close is not None and not pd.isna(ma20) and close < ma20:
        flags.append("收盘价位于 MA20 下方")
    return flags


def build_trade_plan(row: pd.Series) -> dict:
    """按 ATR 生成轻量交易计划。"""

    close = float(row.get("close") or 0.0)
    atr = row.get("atr")
    if close <= 0:
        return {
            "entry_low": 0.0,
            "entry_high": 0.0,
            "stop_loss": 0.0,
            "target1": 0.0,
            "target2": 0.0,
            "rr1": 0.0,
            "rr2": 0.0,
            "position_pct": 0.0,
        }

    if atr is None or pd.isna(atr) or float(atr) <= 0:
        atr = close * 0.02
    risk = max(1.5 * float(atr), close * 0.03)
    stop_loss = max(close - risk, close * 0.85)
    risk = close - stop_loss
    target1 = close + 2.0 * risk
    target2 = close + 3.0 * risk

    score = float(row.get("score") or 0.0)
    position = 6.0 + score / 100.0 * 8.0
    risk_level = str(row.get("risk_level") or "low")
    if risk_level == "high":
        position *= 0.6
    elif risk_level == "medium":
        position *= 0.8
    position = float(np.clip(position, 3.0, 15.0))

    return {
        "entry_low": round(close * 0.99, 2),
        "entry_high": round(close * 1.01, 2),
        "stop_loss": round(stop_loss, 2),
        "target1": round(target1, 2),
        "target2": round(target2, 2),
        "rr1": round((target1 - close) / risk, 2) if risk > 0 else 0.0,
        "rr2": round((target2 - close) / risk, 2) if risk > 0 else 0.0,
        "position_pct": round(position, 1),
    }


def factor_breakdown(row: pd.Series, config: RadarConfig | None = None) -> list[dict]:
    """输出 7 个因子的原始值、百分位、权重与贡献，供评分分解抽屉使用。"""

    config = config or DEFAULT_CONFIG
    items: list[dict] = []
    for key, weight in config.weights.items():
        column = FACTOR_COLUMNS[key]
        raw = row.get(column)
        value: float | None
        if raw is None or pd.isna(raw):
            value = None
        else:
            value = float(raw)
        percentile = float(row.get(f"pct_{key}") or 50.0)
        items.append(
            {
                "key": key,
                "label": FACTOR_LABELS.get(key, key),
                "value": value,
                "value_text": _format_factor(key, value),
                "percentile": round(percentile, 1),
                "weight": weight,
                "contribution": round(percentile * weight / config.weight_sum, 2),
            }
        )
    return items


def _format_factor(key: str, value: float | None) -> str:
    if value is None:
        return "—"
    if key == "momentum_1d":
        return f"{value:+.2f}%"
    if key in {"momentum_5d", "capital_flow", "relative_strength"}:
        return f"{value * 100:+.2f}%"
    if key == "turnover":
        return f"{value:.2f}%"
    if key == "trend":
        return f"{value:.0f} 分"
    return f"{value:.2f}"
