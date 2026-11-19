from __future__ import annotations

import pytest

import app.services.kline_service as kline_module
from app.core.errors import InsufficientHistoryError, InvalidParamError, SymbolNotFoundError


def test_daily_kline_structure(kline_service):
    envelope = kline_service.get_kline("600001.SH", limit=250)
    assert envelope.ok is True
    assert envelope.source == "sample"

    data = envelope.data
    assert data.ts_code == "600001.SH"
    assert data.period == "daily"
    assert data.adjust == "qfq"
    assert data.name.startswith("样本")
    assert len(data.bars) == 170
    assert data.bars[-1].date == "2026-11-18"
    assert data.bars[-1].high >= data.bars[-1].low

    indicators = data.indicators
    assert len(indicators.ma.ma5) == len(data.bars)
    assert len(indicators.boll.upper) == len(data.bars)
    assert len(indicators.macd.hist) == len(data.bars)
    assert len(indicators.rsi) == len(data.bars)
    assert len(indicators.vol_ma.ma5) == len(data.bars)
    assert indicators.ma.ma60[-1] is not None
    assert indicators.rsi[-1] is not None


def test_limit_truncates_aligned_series(kline_service):
    envelope = kline_service.get_kline("600001.SH", limit=30)
    data = envelope.data
    assert len(data.bars) == 30
    assert len(data.indicators.ma.ma20) == 30
    assert len(data.indicators.macd.diff) == 30


def test_periods_resample(kline_service):
    daily = kline_service.get_kline("600001.SH").data
    weekly = kline_service.get_kline("600001.SH", period="weekly").data
    monthly = kline_service.get_kline("600001.SH", period="monthly").data

    assert 0 < len(monthly.bars) <= len(weekly.bars) <= len(daily.bars)
    assert weekly.bars[-1].date >= monthly.bars[-1].date
    for bar in weekly.bars + monthly.bars:
        assert bar.high >= max(bar.open, bar.close)
        assert bar.low <= min(bar.open, bar.close)


def test_symbol_without_suffix_resolves(kline_service):
    data = kline_service.get_kline("600001").data
    assert data.ts_code == "600001.SH"


def test_invalid_period_and_adjust(kline_service):
    with pytest.raises(InvalidParamError):
        kline_service.get_kline("600001.SH", period="quarterly")
    with pytest.raises(InvalidParamError):
        kline_service.get_kline("600001.SH", adjust="none")


def test_unknown_symbol(kline_service):
    with pytest.raises(SymbolNotFoundError):
        kline_service.get_kline("999999.SH")


def test_insufficient_history(kline_service, monkeypatch):
    monkeypatch.setitem(kline_module.MIN_BARS, "daily", 1000)
    with pytest.raises(InsufficientHistoryError):
        kline_service.get_kline("600002.SH")
