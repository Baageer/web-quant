from __future__ import annotations

import pytest

from app.backtest.broker import Broker
from app.backtest.models import ExecutionConfig, FeeConfig


@pytest.fixture()
def broker():
    return Broker(FeeConfig(), ExecutionConfig(slippage_bps=5.0))


def test_slippage_direction(broker):
    assert broker.fill_price(10.0, "buy") == pytest.approx(10.005)
    assert broker.fill_price(10.0, "sell") == pytest.approx(9.995)


def test_zero_slippage_keeps_open_price():
    broker = Broker(FeeConfig(), ExecutionConfig(slippage_bps=0.0))
    assert broker.fill_price(10.0, "buy") == pytest.approx(10.0)
    assert broker.fill_price(10.0, "sell") == pytest.approx(10.0)


def test_minimum_commission(broker):
    fees = broker.fees(1_000.0, "buy")
    assert fees.commission == pytest.approx(5.0)


def test_commission_proportional_for_large_amount(broker):
    fees = broker.fees(100_000.0, "buy")
    assert fees.commission == pytest.approx(25.0)


def test_stamp_duty_only_on_sell(broker):
    buy = broker.fees(100_000.0, "buy")
    sell = broker.fees(100_000.0, "sell")
    assert buy.stamp_duty == pytest.approx(0.0)
    assert sell.stamp_duty == pytest.approx(50.0)


def test_transfer_fee_both_sides(broker):
    buy = broker.fees(100_000.0, "buy")
    sell = broker.fees(100_000.0, "sell")
    assert buy.transfer_fee == pytest.approx(1.0)
    assert sell.transfer_fee == pytest.approx(1.0)


def test_fee_total_components(broker):
    fees = broker.fees(100_000.0, "sell")
    assert fees.total == pytest.approx(25.0 + 50.0 + 1.0)


def test_max_affordable_shares_accounts_for_fees(broker):
    # 1000 股需要 10000 元 + 费用，现金 10000 只能买 900 股
    assert broker.max_affordable_shares(10_000.0, 10.0) == 900
    assert broker.max_affordable_shares(0.0, 10.0) == 0
    assert broker.max_affordable_shares(1_000.0, 10.0) == 0


def test_max_affordable_shares_exact_cash():
    broker = Broker(FeeConfig(), ExecutionConfig(slippage_bps=0.0))
    # 9000 元成交 + 最低佣金 5 元 + 过户费 0.09 元 <= 9006 元现金
    assert broker.max_affordable_shares(9_006.0, 10.0) == 900


def test_slippage_cost_sign(broker):
    buy_cost = broker.slippage_cost(1000, 10.0, 10.005, "buy")
    sell_cost = broker.slippage_cost(1000, 10.0, 9.995, "sell")
    assert buy_cost == pytest.approx(5.0)
    assert sell_cost == pytest.approx(5.0)
