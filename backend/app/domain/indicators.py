"""技术指标纯函数（DESIGN.md 8.5）。

所有函数只依赖传入的 pandas 数据，不做存储访问，便于单测与复用。
"""

from __future__ import annotations

import math

import numpy as np
import pandas as pd

MA_WINDOWS = (5, 10, 20, 60)
VOLUME_WINDOWS = (5, 20)


def _to_series(values) -> pd.Series:
    if isinstance(values, pd.Series):
        return values.astype("float64")
    return pd.Series(np.asarray(values, dtype="float64"))


def sma(values, window: int) -> pd.Series:
    """简单移动平均。"""
    return _to_series(values).rolling(window=window, min_periods=window).mean()


def ema(values, span: int) -> pd.Series:
    """指数移动平均。"""
    return _to_series(values).ewm(span=span, adjust=False).mean()


def macd(
    close,
    fast: int = 12,
    slow: int = 26,
    signal: int = 9,
) -> pd.DataFrame:
    """MACD：diff（DIF）、dea（DEA）、hist（柱线）。"""
    series = _to_series(close)
    diff = ema(series, fast) - ema(series, slow)
    dea = diff.ewm(span=signal, adjust=False).mean()
    hist = (diff - dea) * 2.0
    return pd.DataFrame({"diff": diff, "dea": dea, "hist": hist})


def rsi(close, period: int = 14) -> pd.Series:
    """RSI，使用 Wilder 平滑。"""
    series = _to_series(close)
    delta = series.diff()
    gain = delta.clip(lower=0.0)
    loss = -delta.clip(upper=0.0)
    avg_gain = gain.ewm(alpha=1.0 / period, adjust=False, min_periods=period).mean()
    avg_loss = loss.ewm(alpha=1.0 / period, adjust=False, min_periods=period).mean()
    rs = avg_gain / avg_loss.replace(0.0, np.nan)
    result = 100.0 - 100.0 / (1.0 + rs)
    result = result.where(avg_loss != 0.0, 100.0)
    return result.where(avg_gain.notna())


def boll(close, window: int = 20, num_std: float = 2.0) -> pd.DataFrame:
    """布林带：upper / middle / lower / bandwidth。"""
    series = _to_series(close)
    middle = series.rolling(window=window, min_periods=window).mean()
    std = series.rolling(window=window, min_periods=window).std(ddof=0)
    upper = middle + num_std * std
    lower = middle - num_std * std
    bandwidth = (upper - lower) / middle.replace(0.0, np.nan)
    return pd.DataFrame(
        {"upper": upper, "middle": middle, "lower": lower, "bandwidth": bandwidth}
    )


def true_range(bars: pd.DataFrame) -> pd.Series:
    """真实波幅 TR = max(high-low, |high-prev_close|, |low-prev_close|)。"""
    high = _to_series(bars["high"])
    low = _to_series(bars["low"])
    close = _to_series(bars["close"])
    prev_close = close.shift(1)
    if "pre_close" in bars.columns:
        stored = _to_series(bars["pre_close"])
        prev_close = stored.where(stored.notna(), prev_close)
    ranges = pd.concat(
        [(high - low).abs(), (high - prev_close).abs(), (low - prev_close).abs()],
        axis=1,
    )
    return ranges.max(axis=1)


def atr(bars: pd.DataFrame, period: int = 14) -> pd.Series:
    """ATR，使用 Wilder 平滑。"""
    tr = true_range(bars)
    return tr.ewm(alpha=1.0 / period, adjust=False, min_periods=period).mean()


def kdj(
    bars: pd.DataFrame,
    n: int = 9,
    k_period: int = 3,
    d_period: int = 3,
) -> pd.DataFrame:
    """KDJ：RSV 的递归平滑。"""
    close = _to_series(bars["close"])
    low_n = _to_series(bars["low"]).rolling(window=n, min_periods=n).min()
    high_n = _to_series(bars["high"]).rolling(window=n, min_periods=n).max()
    span = (high_n - low_n).replace(0.0, np.nan)
    rsv = (close - low_n) / span * 100.0
    k = rsv.ewm(alpha=1.0 / k_period, adjust=False).mean()
    d = k.ewm(alpha=1.0 / d_period, adjust=False).mean()
    j = 3.0 * k - 2.0 * d
    return pd.DataFrame({"k": k, "d": d, "j": j})


def volume_ma(volume, windows: tuple[int, ...] = VOLUME_WINDOWS) -> pd.DataFrame:
    """成交量均线。"""
    series = _to_series(volume)
    return pd.DataFrame({f"ma{window}": sma(series, window) for window in windows})


def compute_indicators(bars: pd.DataFrame) -> dict[str, pd.DataFrame | pd.Series]:
    """一次性计算 K 线页所需全部指标，索引与 bars 对齐。"""
    close = bars["close"]
    return {
        "ma": pd.DataFrame(
            {f"ma{window}": sma(close, window) for window in MA_WINDOWS}
        ),
        "boll": boll(close),
        "macd": macd(close),
        "rsi": rsi(close),
        "atr": atr(bars),
        "kdj": kdj(bars),
        "vol_ma": volume_ma(bars["vol"]),
    }


def series_to_list(series: pd.Series, digits: int = 4) -> list[float | None]:
    """把指标序列转为 JSON 友好列表，NaN/Inf 转为 None。"""
    output: list[float | None] = []
    for value in series.tolist():
        if value is None:
            output.append(None)
            continue
        try:
            number = float(value)
        except (TypeError, ValueError):
            output.append(None)
            continue
        if math.isnan(number) or math.isinf(number):
            output.append(None)
        else:
            output.append(round(number, digits))
    return output
