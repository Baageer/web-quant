from __future__ import annotations

import math

import numpy as np
import pandas as pd
import pytest

from app.domain.indicators import (
    atr,
    boll,
    compute_indicators,
    ema,
    kdj,
    macd,
    rsi,
    series_to_list,
    sma,
    volume_ma,
)


def test_sma_and_ema():
    values = pd.Series([1.0, 2.0, 3.0, 4.0, 5.0])
    result = sma(values, 3)
    assert math.isnan(result.iloc[0])
    assert result.iloc[2] == pytest.approx(2.0)
    assert result.iloc[-1] == pytest.approx(4.0)

    smoothed = ema(values, 3)
    assert smoothed.iloc[0] == pytest.approx(1.0)
    assert smoothed.iloc[-1] > smoothed.iloc[-2]


def test_macd_columns_and_relationship():
    close = pd.Series(np.linspace(10, 20, 80))
    frame = macd(close)
    assert list(frame.columns) == ["diff", "dea", "hist"]
    assert frame["hist"].iloc[-1] == pytest.approx(
        (frame["diff"].iloc[-1] - frame["dea"].iloc[-1]) * 2.0
    )


def test_rsi_bounds():
    close = pd.Series(np.linspace(10, 30, 60) + np.sin(np.arange(60)))
    values = rsi(close).dropna()
    assert not values.empty
    assert values.min() >= 0.0
    assert values.max() <= 100.0


def test_boll_ordering():
    close = pd.Series(np.linspace(10, 20, 60))
    frame = boll(close).dropna()
    assert (frame["upper"] >= frame["middle"]).all()
    assert (frame["middle"] >= frame["lower"]).all()
    assert frame["bandwidth"].iloc[-1] > 0


def test_atr_positive():
    close = pd.Series(np.linspace(10, 12, 40))
    bars = pd.DataFrame(
        {
            "high": close + 0.2,
            "low": close - 0.2,
            "close": close,
            "pre_close": close.shift(1),
        }
    )
    values = atr(bars).dropna()
    assert not values.empty
    assert (values > 0).all()


def test_kdj_and_volume_ma():
    close = pd.Series(np.linspace(10, 15, 40))
    bars = pd.DataFrame({"high": close + 0.1, "low": close - 0.1, "close": close})
    frame = kdj(bars)
    assert list(frame.columns) == ["k", "d", "j"]
    valid = frame.dropna()
    assert valid["k"].between(-50, 150).all()

    volumes = pd.Series(np.arange(1, 41, dtype="float64"))
    vol_frame = volume_ma(volumes)
    assert list(vol_frame.columns) == ["ma5", "ma20"]
    assert vol_frame["ma5"].iloc[-1] == pytest.approx(38.0)


def test_compute_indicators_alignment():
    close = pd.Series(np.linspace(10, 20, 80))
    bars = pd.DataFrame(
        {
            "high": close + 0.3,
            "low": close - 0.3,
            "close": close,
            "vol": np.linspace(1000, 2000, 80),
        }
    )
    indicators = compute_indicators(bars)
    assert set(indicators) == {"ma", "boll", "macd", "rsi", "atr", "kdj", "vol_ma"}
    for frame in indicators.values():
        assert len(frame) == len(bars)


def test_series_to_list_handles_nan():
    values = pd.Series([1.23456, np.nan, np.inf, -np.inf, 2.0])
    result = series_to_list(values, digits=2)
    assert result == [1.23, None, None, None, 2.0]
