"""撮合与费用（见 docs/BACKTEST_DESIGN.md §5.3 / §5.5）。

- 买入价 = 开盘价 × (1 + 滑点)；卖出价 = 开盘价 × (1 - 滑点)；
- 佣金 max(成交额 × 费率, 最低佣金)；印花税仅卖出；过户费双边；
- 资金不足时按整手缩量，缩到 0 由引擎记为拒单。
"""

from __future__ import annotations

from dataclasses import dataclass

from app.backtest.constraints import round_lot
from app.backtest.models import ACTION_BUY, FeeConfig, ExecutionConfig


@dataclass(frozen=True)
class FeeBreakdown:
    commission: float = 0.0
    stamp_duty: float = 0.0
    transfer_fee: float = 0.0

    @property
    def total(self) -> float:
        return self.commission + self.stamp_duty + self.transfer_fee


class Broker:
    def __init__(self, fees: FeeConfig, execution: ExecutionConfig) -> None:
        self.fees_config = fees
        self.execution = execution

    # ---- 价格 ----

    def fill_price(self, open_price: float, action: str) -> float:
        if open_price is None or open_price <= 0:
            return 0.0
        rate = self.execution.slippage_bps / 10_000.0
        if action == ACTION_BUY:
            return round(float(open_price) * (1.0 + rate), 4)
        return round(float(open_price) * (1.0 - rate), 4)

    def slippage_cost(
        self, shares: int, open_price: float, fill_price: float, action: str
    ) -> float:
        if shares <= 0:
            return 0.0
        sign = 1.0 if action == ACTION_BUY else -1.0
        return round(sign * (float(fill_price) - float(open_price)) * shares, 2)

    # ---- 费用 ----

    def fees(self, amount: float, action: str) -> FeeBreakdown:
        cfg = self.fees_config
        if amount <= 0:
            return FeeBreakdown()
        commission = max(amount * cfg.commission_rate, cfg.commission_min)
        stamp_duty = amount * cfg.stamp_duty_sell if action != ACTION_BUY else 0.0
        transfer_fee = amount * cfg.transfer_fee
        return FeeBreakdown(
            commission=round(commission, 2),
            stamp_duty=round(stamp_duty, 2),
            transfer_fee=round(transfer_fee, 2),
        )

    def max_affordable_shares(self, cash: float, price: float) -> int:
        """现金可买的最大整手数量（含买入费用）。"""
        if cash <= 0 or price <= 0:
            return 0
        cfg = self.fees_config
        gross_rate = 1.0 + cfg.commission_rate + cfg.transfer_fee
        shares = round_lot(cash / (price * gross_rate))
        while shares > 0:
            amount = shares * price
            if amount + self.fees(amount, ACTION_BUY).total <= cash + 1e-9:
                return shares
            shares -= 100
        return 0
