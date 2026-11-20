from __future__ import annotations

import pandas as pd
import pytest

from app.backtest import constraints
from app.backtest.portfolio import Portfolio


def test_round_lot_floor_to_hundred():
    assert constraints.round_lot(0) == 0
    assert constraints.round_lot(99) == 0
    assert constraints.round_lot(150) == 100
    assert constraints.round_lot(1999) == 1900
    assert constraints.round_lot(-100) == 0


def test_limit_threshold_by_board_and_st():
    assert constraints.limit_threshold("600001.SH") == pytest.approx(0.10)
    assert constraints.limit_threshold("300001.SZ") == pytest.approx(0.20)
    assert constraints.limit_threshold("688001.SH") == pytest.approx(0.20)
    assert constraints.limit_threshold("830001.BJ") == pytest.approx(0.30)
    assert constraints.limit_threshold("600001.SH", "ST样本") == pytest.approx(0.05)


def test_limit_price_rounding():
    assert constraints.limit_up_price(10.0, "600001.SH") == pytest.approx(11.0)
    assert constraints.limit_down_price(10.0, "600001.SH") == pytest.approx(9.0)
    assert constraints.limit_up_price(27.17, "600001.SH") == pytest.approx(29.89)
    assert constraints.limit_up_price(10.0, "600001.SH", "ST样本") == pytest.approx(10.5)


def test_at_limit_up_and_down():
    assert constraints.at_limit_up(11.0, 10.0, "600001.SH") is True
    assert constraints.at_limit_up(10.99, 10.0, "600001.SH") is False
    assert constraints.at_limit_down(9.0, 10.0, "600001.SH") is True
    assert constraints.at_limit_down(9.01, 10.0, "600001.SH") is False


def test_is_suspended_cases():
    assert constraints.is_suspended(None) is True
    assert constraints.is_suspended({"open": 10.0, "vol": 0}) is True
    assert constraints.is_suspended({"open": 0.0, "vol": 100}) is True
    assert constraints.is_suspended({"open": float("nan"), "vol": 100}) is True
    assert constraints.is_suspended(pd.Series({"open": 10.0, "vol": 100})) is False


def test_check_open_bar_rejects_limit_and_suspension():
    bar = pd.Series({"open": 11.0, "pre_close": 10.0, "vol": 100})
    assert constraints.check_open_bar(bar, "buy", "600001.SH") == "limit_up"

    bar_down = pd.Series({"open": 9.0, "pre_close": 10.0, "vol": 100})
    assert constraints.check_open_bar(bar_down, "sell", "600001.SH") == "limit_down"

    assert constraints.check_open_bar(None, "buy", "600001.SH") == "suspended"


def test_sellable_shares_respects_t_plus_one():
    assert constraints.sellable_shares(1000, 0) == 0
    assert constraints.sellable_shares(1000, 600) == 600
    assert constraints.sellable_shares(1000, 2000) == 1000


def test_portfolio_t_plus_one_cycle():
    portfolio = Portfolio(100_000.0)
    fees = type("Fees", (), {"total": 5.0})()
    portfolio.buy("600001.SH", 1000, 10.0, fees)
    position = portfolio.positions["600001.SH"]

    assert position.shares == 1000
    assert position.available == 0
    assert constraints.sellable_shares(position.shares, position.available) == 0

    portfolio.settle()
    assert position.available == 1000
    assert constraints.sellable_shares(position.shares, position.available) == 1000
