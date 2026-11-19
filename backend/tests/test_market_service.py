from __future__ import annotations

import pytest

from app.core.errors import DataNotReadyError, InvalidParamError
from app.data.universe import board_of, limit_threshold


def test_overview_contains_all_sections(market_service):
    envelope = market_service.get_overview()
    assert envelope.ok is True
    assert envelope.source == "sample"
    data = envelope.data

    assert data.trade_date == "20261118"
    assert len(data.indices) == 6
    assert data.breadth.total > 0
    assert data.breadth.up + data.breadth.down + data.breadth.flat == data.breadth.total
    assert data.amount.total > 0
    assert "亿元" == data.amount.unit
    assert data.sectors_top
    assert data.sectors_bottom
    assert data.leaders.gainers
    assert data.leaders.losers
    assert data.leaders.turnover
    assert data.regime.label in {"risk_on", "neutral", "risk_off"}


def test_sectors_are_sorted(market_service):
    data = market_service.get_sectors(limit=5).data
    assert data.top
    assert data.bottom
    top_values = [item.pct_chg for item in data.top]
    bottom_values = [item.pct_chg for item in data.bottom]
    assert top_values == sorted(top_values, reverse=True)
    assert bottom_values == sorted(bottom_values)


def test_tickers_sort_and_search(market_service):
    envelope = market_service.get_tickers(limit=10, sort="pctChg", order="desc")
    items = envelope.data.items
    assert len(items) == 10
    assert envelope.data.total == 80
    values = [item.pct_chg for item in items]
    assert values == sorted(values, reverse=True)

    searched = market_service.get_tickers(limit=50, search="样本银行").data
    assert searched.total > 0
    assert all("银行" in (item.name or "") for item in searched.items)


def test_tickers_reject_unknown_sort(market_service):
    with pytest.raises(InvalidParamError):
        market_service.get_tickers(sort="unknown")


def test_resolve_trade_date_handles_aliases(market_service):
    assert market_service.resolve_trade_date("20261118") == "20261118"
    assert market_service.resolve_trade_date("2026-11-18") == "20261118"
    with pytest.raises(DataNotReadyError):
        market_service.resolve_trade_date("20200101")


def test_stock_rules():
    assert board_of("600000.SH") == "main"
    assert board_of("300001.SZ") == "chinext"
    assert board_of("688001.SH") == "star"
    assert limit_threshold("600000.SH", "浦发银行") == pytest.approx(0.10)
    assert limit_threshold("300001.SZ", "某创业板") == pytest.approx(0.20)
    assert limit_threshold("600001.SH", "ST样本") == pytest.approx(0.05)
