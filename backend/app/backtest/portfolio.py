"""账户、持仓、目标权重与估值（见 docs/BACKTEST_DESIGN.md §5.4 / §7）。

T+1 的实现方式：
  - 每个交易日开盘前调用 settle()，把上一交易日买入的股份转为可用；
  - 当日买入只增加 shares，不增加 available，因此当日不可卖。

`constrain_targets` 负责目标权重的组合约束：单票上限 → 行业上限 → 持仓数 → 换手上限；
现金约束由引擎在撮合时按整手缩量处理。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping

from app.backtest.broker import FeeBreakdown


@dataclass
class Position:
    shares: int = 0
    available: int = 0
    cost: float = 0.0  # 持仓总成本（含买入费用）
    last_price: float = 0.0

    @property
    def avg_cost(self) -> float:
        return self.cost / self.shares if self.shares > 0 else 0.0

    def market_value(self, price: float | None = None) -> float:
        used = price if price and price > 0 else self.last_price
        return self.shares * used


class Portfolio:
    def __init__(self, cash: float) -> None:
        self.initial_cash = float(cash)
        self.cash = float(cash)
        self.positions: dict[str, Position] = {}

    # ---- 日初结算 ----

    def settle(self) -> None:
        """T+1：把昨日及更早买入的股份全部转为可卖。"""
        for position in self.positions.values():
            position.available = position.shares

    # ---- 估值 ----

    def equity(self, prices: dict[str, float]) -> float:
        value = self.cash
        for code, position in self.positions.items():
            if position.shares <= 0:
                continue
            value += position.market_value(prices.get(code))
        return value

    def market_value(self, prices: dict[str, float]) -> float:
        return self.equity(prices) - self.cash

    # ---- 成交 ----

    def buy(self, ts_code: str, shares: int, price: float, fees: FeeBreakdown) -> None:
        if shares <= 0:
            return
        amount = shares * price
        self.cash -= amount + fees.total
        position = self.positions.setdefault(ts_code, Position())
        position.shares += shares
        position.cost += amount + fees.total
        position.last_price = price

    def sell(self, ts_code: str, shares: int, price: float, fees: FeeBreakdown) -> float:
        """卖出并返回本次实现盈亏（已扣卖出费用）。"""
        position = self.positions.get(ts_code)
        if position is None or shares <= 0:
            return 0.0
        shares = min(int(shares), position.shares)
        if shares <= 0:
            return 0.0
        avg_cost = position.avg_cost
        amount = shares * price
        self.cash += amount - fees.total
        position.shares -= shares
        position.available = max(0, position.available - shares)
        position.cost -= avg_cost * shares
        position.last_price = price
        if position.shares <= 0:
            self.positions.pop(ts_code, None)
        return amount - fees.total - avg_cost * shares

    # ---- 快照 ----

    def snapshot(self, date: str, prices: dict[str, float]) -> list[dict]:
        total = self.equity(prices)
        rows: list[dict] = []
        for code, position in self.positions.items():
            if position.shares <= 0:
                continue
            close = prices.get(code) or position.last_price
            value = position.market_value(close)
            rows.append(
                {
                    "date": date,
                    "ts_code": code,
                    "shares": int(position.shares),
                    "weight": value / total if total > 0 else 0.0,
                    "close": round(float(close), 4),
                    "market_value": round(value, 2),
                }
            )
        return rows


def constrain_targets(
    target: Mapping[str, float],
    *,
    current_weights: Mapping[str, float],
    industry_of: Mapping[str, str] | None = None,
    max_position: float = 0.10,
    max_industry: float = 0.30,
    max_positions: int = 20,
    max_turnover: float | None = None,
) -> tuple[dict[str, float], list[str]]:
    """按 §7.1 的顺序应用组合约束，返回 (目标权重, 提示列表)。"""
    warnings: list[str] = []

    # 1. 单票上限
    capped: dict[str, float] = {}
    single_capped = False
    for code, weight in target.items():
        value = float(weight)
        if value <= 0:
            continue
        if value > max_position:
            value = max_position
            single_capped = True
        capped[str(code)] = value
    if single_capped:
        warnings.append(f"触发单票上限 {max_position:.0%}，已截断")

    # 2. 行业上限（无行业信息的标的不参与行业约束）
    industry_capped = False
    if industry_of and max_industry > 0:
        totals: dict[str, float] = {}
        adjusted: dict[str, float] = {}
        for code, weight in sorted(capped.items(), key=lambda item: item[1], reverse=True):
            industry = str(industry_of.get(code) or "").strip()
            if not industry:
                adjusted[code] = weight
                continue
            room = max_industry - totals.get(industry, 0.0)
            if room <= 1e-9:
                industry_capped = True
                continue
            applied = min(weight, room)
            if applied < weight - 1e-9:
                industry_capped = True
            totals[industry] = totals.get(industry, 0.0) + applied
            adjusted[code] = applied
        capped = adjusted
    if industry_capped:
        warnings.append(f"触发行业上限 {max_industry:.0%}，已按权重截断")

    # 3. 最大持仓数
    ranked = sorted(capped.items(), key=lambda item: item[1], reverse=True)
    if max_positions > 0 and len(ranked) > max_positions:
        ranked = ranked[:max_positions]
        warnings.append(f"触发最大持仓数 {max_positions}，已保留权重最高的标的")
    result = dict(ranked)

    # 4. 换手上限（单边换手 = Σ|目标-当前| / 2）
    if max_turnover is not None and max_turnover >= 0:
        codes = set(result) | set(current_weights)
        turnover = (
            sum(
                abs(result.get(code, 0.0) - float(current_weights.get(code, 0.0)))
                for code in codes
            )
            / 2.0
        )
        if turnover > max_turnover > 0:
            scale = max_turnover / turnover
            result = {
                code: float(current_weights.get(code, 0.0))
                + (weight - float(current_weights.get(code, 0.0))) * scale
                for code, weight in result.items()
            }
            result = {
                code: weight for code, weight in result.items() if weight > 1e-6
            }
            warnings.append(f"触发换手上限 {max_turnover:.0%}，已按比例缩减调仓幅度")

    return result, warnings
