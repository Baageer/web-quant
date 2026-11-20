"""逐交易日事件循环（见 docs/BACKTEST_DESIGN.md §5）。

时间线：T 日收盘生成信号 → T+1 开盘撮合 → T 日收盘估值。
订单只会出现在 T+1 及之后，引擎在撮合前断言 trade_date > signal_date。
"""

from __future__ import annotations

from typing import Iterable

import pandas as pd

from app.core.logging import get_logger
from app.backtest import constraints
from app.backtest.broker import Broker
from app.backtest.data_loader import Panel
from app.backtest.models import (
    ACTION_BUY,
    ACTION_SELL,
    NAV_COLUMNS,
    ORDER_EXPIRED,
    ORDER_FILLED,
    ORDER_REJECTED,
    POSITION_COLUMNS,
    REASON_INSUFFICIENT_CASH,
    REASON_NOT_IN_UNIVERSE,
    REASON_SIGNAL_EXPIRED,
    REASON_SUSPENDED,
    REASON_T_PLUS_ONE,
    TRADE_COLUMNS,
    BacktestConfig,
    EngineOutput,
    Order,
    TradeRecord,
)
from app.backtest.portfolio import Portfolio, constrain_targets
from app.backtest.strategies.base import Strategy

logger = get_logger(__name__)


class LookaheadError(RuntimeError):
    """成交日期不晚于信号日期时抛出。"""


class BacktestEngine:
    def __init__(self, panel: Panel, strategy: Strategy, config: BacktestConfig) -> None:
        self.panel = panel
        self.strategy = strategy
        self.config = config
        self.broker = Broker(config.fees, config.execution)
        self._warnings: list[str] = []

    # ---- 主循环 ----

    def run(self) -> EngineOutput:
        self.strategy.prepare(self.panel, dict(self.config.params))
        self._deferred = 0
        portfolio = Portfolio(self.config.initial_cash)
        pending: list[Order] = []
        trades: list[TradeRecord] = []
        nav_rows: list[dict] = []
        position_rows: list[dict] = []
        dates = self.panel.dates
        peak = 1.0
        previous_nav = 1.0
        ignored_short = 0

        for index, date in enumerate(dates):
            portfolio.settle()
            if pending:
                pending = self._execute(pending, date, portfolio, trades)

            closes = self.panel.close_prices(date)
            equity = portfolio.equity(closes)
            nav = equity / self.config.initial_cash if self.config.initial_cash else 0.0
            peak = max(peak, nav)
            nav_rows.append(
                {
                    "date": date,
                    "portfolio_value": round(equity, 2),
                    "nav": round(nav, 6),
                    "cash": round(portfolio.cash, 2),
                    "cash_ratio": round(portfolio.cash / equity, 6) if equity > 0 else 0.0,
                    "daily_return": round(nav / previous_nav - 1.0, 6) if previous_nav else 0.0,
                    "drawdown": round(nav / peak - 1.0, 6) if peak > 0 else 0.0,
                }
            )
            previous_nav = nav
            position_rows.extend(portfolio.snapshot(date, closes))

            if index + 1 >= len(dates):
                break
            raw_target, ignored = self._sanitize(
                self.strategy.signal(date, self.panel.as_of(date))
            )
            ignored_short += ignored
            current_weights = {
                code: position.market_value(closes.get(code)) / equity
                for code, position in portfolio.positions.items()
                if position.shares > 0 and equity > 0
            }
            target, notes = constrain_targets(
                raw_target,
                current_weights=current_weights,
                industry_of=self.panel.industry_map,
                max_position=self.config.max_position,
                max_industry=self.config.max_industry,
                max_positions=self.config.max_positions,
                max_turnover=self.config.max_turnover,
            )
            for note in notes:
                self._warn(note)
            pending = self._make_orders(
                target, date, dates[index + 1], portfolio, pending
            )

        if ignored_short:
            self._warn(f"忽略 {ignored_short} 个做空/无效权重信号（P4 不支持下单）")
        if not trades:
            self._warn("回测期内没有产生任何订单")

        nav_frame = pd.DataFrame(nav_rows, columns=NAV_COLUMNS)
        trade_frame = pd.DataFrame(
            [record.to_dict() for record in trades], columns=TRADE_COLUMNS
        )
        position_frame = pd.DataFrame(position_rows, columns=POSITION_COLUMNS)
        return EngineOutput(
            nav=nav_frame,
            trades=trade_frame,
            positions=position_frame,
            warnings=self._unique(self.panel.warnings + self._warnings),
            deferred_count=self._deferred,
        )

    # ---- 信号与下单 ----

    def _sanitize(self, raw: dict[str, float] | None) -> tuple[dict[str, float], int]:
        """只保留有限的做多权重；组合约束由 constrain_targets 统一处理。"""
        target: dict[str, float] = {}
        ignored = 0
        for code, weight in (raw or {}).items():
            try:
                value = float(weight)
            except (TypeError, ValueError):
                ignored += 1
                continue
            if pd.isna(value) or value <= 0:
                ignored += 1
                continue
            target[str(code)] = value
        return target, ignored

    def _make_orders(
        self,
        target: dict[str, float],
        signal_date: str,
        eligible_date: str,
        portfolio: Portfolio,
        pending: list[Order],
    ) -> list[Order]:
        ttl = max(1, int(self.config.execution.signal_ttl_days))
        kept: list[Order] = []
        kept_codes: set[str] = set()

        # 停牌顺延的旧订单：目标未消失则保留，避免每天重复生成
        for order in pending:
            if constraints.is_suspended(self.panel.bar(signal_date, order.ts_code)):
                # 当日停牌看不到新信号，按 TTL 顺延（见 §5.2）
                kept.append(order)
                kept_codes.add(order.ts_code)
                continue
            if order.action == "target" and order.ts_code not in target:
                continue
            if order.action == "sell_all" and order.ts_code in target:
                continue
            kept.append(order)
            kept_codes.add(order.ts_code)

        for code, weight in target.items():
            if code in kept_codes:
                continue
            kept.append(
                Order(
                    ts_code=code,
                    signal_date=signal_date,
                    action="target",
                    target_weight=weight,
                    eligible_date=eligible_date,
                    ttl_days=ttl,
                )
            )

        for code, position in portfolio.positions.items():
            if position.shares <= 0 or code in target or code in kept_codes:
                continue
            kept.append(
                Order(
                    ts_code=code,
                    signal_date=signal_date,
                    action="sell_all",
                    target_weight=0.0,
                    eligible_date=eligible_date,
                    ttl_days=ttl,
                )
            )
        return kept

    # ---- 撮合 ----

    def _execute(
        self,
        pending: list[Order],
        date: str,
        portfolio: Portfolio,
        trades: list[TradeRecord],
    ) -> list[Order]:
        remaining: list[Order] = []
        equity_open: float | None = None

        for order in pending:
            if order.eligible_date and date < order.eligible_date:
                remaining.append(order)
                continue
            if date <= order.signal_date:
                raise LookaheadError(
                    f"{order.ts_code} 成交日 {date} 不晚于信号日 {order.signal_date}"
                )

            code = order.ts_code
            bar = self.panel.bar(date, code)
            name = None if bar is None else bar.get("name")
            order.attempts += 1

            if constraints.is_suspended(bar):
                if order.attempts >= max(1, order.ttl_days):
                    trades.append(
                        self._record(
                            order,
                            date,
                            action=self._fallback_action(order, portfolio),
                            status=ORDER_EXPIRED,
                            reason=REASON_SIGNAL_EXPIRED,
                            note=f"停牌超过信号有效期 {order.ttl_days} 个交易日",
                        )
                    )
                else:
                    remaining.append(order)
                    self._deferred += 1
                continue

            if equity_open is None:
                equity_open = portfolio.equity(self.panel.open_prices(date))
            open_price = float(bar["open"])

            if order.action == "sell_all":
                if self._handle_sell_all(
                    order, bar, name, date, open_price, portfolio, trades
                ):
                    remaining.append(order)
                continue

            kept = self._handle_target(
                order, bar, name, date, open_price, equity_open, portfolio, trades
            )
            if kept:
                remaining.append(order)

        return remaining

    def _handle_target(
        self,
        order: Order,
        bar: pd.Series,
        name,
        date: str,
        open_price: float,
        equity_open: float,
        portfolio: Portfolio,
        trades: list[TradeRecord],
    ) -> bool:
        """按目标权重调整仓位；返回 True 表示订单需要顺延。"""
        position = portfolio.positions.get(order.ts_code)
        current = position.shares if position else 0
        target_shares = constraints.round_lot(
            equity_open * order.target_weight / open_price
        )

        if target_shares == current:
            return False

        if target_shares > current:
            if not self.panel.is_tradable(date, order.ts_code):
                trades.append(
                    self._reject(
                        order, date, ACTION_BUY, REASON_NOT_IN_UNIVERSE, target_shares - current
                    )
                )
                return False
            reason = constraints.check_open_bar(bar, ACTION_BUY, order.ts_code, name)
            if reason:
                trades.append(
                    self._reject(order, date, ACTION_BUY, reason, target_shares - current)
                )
                return False
            price = self.broker.fill_price(open_price, ACTION_BUY)
            wanted = target_shares - current
            affordable = self.broker.max_affordable_shares(portfolio.cash, price)
            shares = min(wanted, affordable)
            if shares < constraints.LOT_SIZE:
                trades.append(
                    self._reject(
                        order, date, ACTION_BUY, REASON_INSUFFICIENT_CASH, wanted
                    )
                )
                return False
            self._fill(order, date, ACTION_BUY, shares, price, open_price, portfolio, trades)
            return False

        # 减仓 / 清仓
        reason = constraints.check_open_bar(bar, ACTION_SELL, order.ts_code, name)
        if reason:
            trades.append(
                self._reject(order, date, ACTION_SELL, reason, current - target_shares)
            )
            return False
        sellable = constraints.sellable_shares(current, position.available)
        if sellable <= 0:
            trades.append(
                self._reject(order, date, ACTION_SELL, REASON_T_PLUS_ONE, current - target_shares)
            )
            return False
        shares = min(current - target_shares, sellable)
        if target_shares > 0:
            shares = constraints.round_lot(shares)
        if shares <= 0:
            return False
        self._fill(order, date, ACTION_SELL, shares, self.broker.fill_price(open_price, ACTION_SELL), open_price, portfolio, trades)
        return False

    def _handle_sell_all(
        self,
        order: Order,
        bar: pd.Series,
        name,
        date: str,
        open_price: float,
        portfolio: Portfolio,
        trades: list[TradeRecord],
    ) -> bool:
        position = portfolio.positions.get(order.ts_code)
        if position is None or position.shares <= 0:
            return False
        reason = constraints.check_open_bar(bar, ACTION_SELL, order.ts_code, name)
        if reason:
            trades.append(
                self._reject(order, date, ACTION_SELL, reason, position.shares)
            )
            return False
        shares = constraints.sellable_shares(position.shares, position.available)
        if shares <= 0:
            trades.append(
                self._reject(order, date, ACTION_SELL, REASON_T_PLUS_ONE, position.shares)
            )
            return False
        price = self.broker.fill_price(open_price, ACTION_SELL)
        self._fill(order, date, ACTION_SELL, shares, price, open_price, portfolio, trades)
        # 清仓允许零股，正常情况下一次卖完
        return position.shares > 0

    # ---- 记录 ----

    def _fill(
        self,
        order: Order,
        date: str,
        action: str,
        shares: int,
        price: float,
        open_price: float,
        portfolio: Portfolio,
        trades: list[TradeRecord],
    ) -> None:
        amount = round(shares * price, 2)
        fees = self.broker.fees(amount, action)
        profit = 0.0
        if action == ACTION_BUY:
            portfolio.buy(order.ts_code, shares, price, fees)
        else:
            profit = round(portfolio.sell(order.ts_code, shares, price, fees), 2)
        trades.append(
            TradeRecord(
                date=date,
                signal_date=order.signal_date,
                ts_code=order.ts_code,
                action=action,
                status=ORDER_FILLED,
                shares=int(shares),
                price=price,
                amount=amount,
                commission=fees.commission,
                stamp_duty=fees.stamp_duty,
                transfer_fee=fees.transfer_fee,
                slippage_cost=self.broker.slippage_cost(
                    shares, open_price, price, action
                ),
                profit=profit,
            )
        )

    def _reject(
        self,
        order: Order,
        date: str,
        action: str,
        reason: str,
        shares: int,
    ) -> TradeRecord:
        return self._record(
            order,
            date,
            action=action,
            status=ORDER_REJECTED,
            reason=reason,
            shares=int(shares),
        )

    def _record(
        self,
        order: Order,
        date: str,
        *,
        action: str,
        status: str,
        reason: str,
        shares: int = 0,
        note: str = "",
    ) -> TradeRecord:
        return TradeRecord(
            date=date,
            signal_date=order.signal_date,
            ts_code=order.ts_code,
            action=action,
            status=status,
            rejection_reason=reason,
            shares=shares,
            reason=note,
        )

    @staticmethod
    def _fallback_action(order: Order, portfolio: Portfolio) -> str:
        if order.action == "sell_all":
            return ACTION_SELL
        position = portfolio.positions.get(order.ts_code)
        return ACTION_SELL if position else ACTION_BUY

    # ---- 工具 ----

    def _warn(self, message: str) -> None:
        logger.info("回测提示：%s", message)
        self._warnings.append(message)

    @staticmethod
    def _unique(items: Iterable[str]) -> list[str]:
        seen: set[str] = set()
        output: list[str] = []
        for item in items:
            if item and item not in seen:
                seen.add(item)
                output.append(item)
        return output
