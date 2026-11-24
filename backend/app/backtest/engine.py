"""逐交易日事件循环（见 docs/BACKTEST_DESIGN.md §5）。

时间线：T 日收盘生成信号 → T+1 开盘撮合 → T 日收盘估值。
订单只会出现在 T+1 及之后，引擎在撮合前断言 trade_date > signal_date。
"""

from __future__ import annotations

from datetime import datetime
from typing import Iterable

import pandas as pd

from app.core.logging import get_logger
from app.backtest import constraints
from app.backtest.broker import Broker
from app.backtest.data_loader import Panel
from app.backtest.models import (
    ACTION_BUY,
    ACTION_SELL,
    ACTION_STOP,
    COOLDOWN_MONTH,
    COOLDOWN_NONE,
    EXIT_ATR_TRAIL,
    EXIT_STOP_LOSS,
    EXIT_TAKE_PROFIT,
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
    REBALANCE_DAILY,
    REBALANCE_WEEKLY,
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


def _is_rebalance_day(mode: str, dates: list[str], index: int) -> bool:
    """判断 index 是否为调仓日（见 §7.2）。

    周/月调仓取交易日历中该周/月的最后一个交易日生成信号，
    仍由引擎延迟到下一交易日开盘成交。
    """
    if mode == REBALANCE_DAILY:
        return True
    if index + 1 >= len(dates):
        return True
    current = _trade_day(dates[index])
    following = _trade_day(dates[index + 1])
    if current is None or following is None:
        return True
    if mode == REBALANCE_WEEKLY:
        return current.isocalendar()[:2] != following.isocalendar()[:2]
    return (current.year, current.month) != (following.year, following.month)


def _trade_day(value: str) -> datetime | None:
    try:
        return datetime.strptime(str(value).replace("-", ""), "%Y%m%d")
    except ValueError:
        return None


class BacktestEngine:
    def __init__(self, panel: Panel, strategy: Strategy, config: BacktestConfig) -> None:
        self.panel = panel
        self.strategy = strategy
        self.config = config
        self.broker = Broker(config.fees, config.execution)
        self._warnings: list[str] = []
        self._peaks: dict[str, float] = {}
        self._cooldown: dict[str, str] = {}
        self._cooldown_blocks = 0

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
            if index > 0:
                self._adjust_corporate_actions(dates[index - 1], date, portfolio)
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
            self._sync_peaks(portfolio, closes)
            pending = self._apply_risk_controls(
                date, dates[index + 1], portfolio, closes, pending
            )
            if not _is_rebalance_day(self.config.rebalance, dates, index):
                continue
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
        if self._cooldown_blocks:
            self._warn(f"止损冷却期内过滤 {self._cooldown_blocks} 个买入信号")
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

    def _adjust_corporate_actions(
        self, previous: str, date: str, portfolio: Portfolio
    ) -> None:
        """按复权因子变化调整持股数量（见 §5.4）。

        送转/分红除权后原始价格会跳变，若持股数不变会产生虚假亏损；
        这里用 adj_factor 比值同步扩缩股份，使持仓市值连续。
        """
        if not portfolio.positions:
            return
        for code, position in list(portfolio.positions.items()):
            before = self.panel.adj_factor(previous, code)
            after = self.panel.adj_factor(date, code)
            if before is None or after is None:
                continue
            ratio = after / before
            if not (0.01 < ratio < 100) or abs(ratio - 1.0) < 1e-9:
                continue
            position.shares = int(round(position.shares * ratio))
            position.available = position.shares
            if position.shares <= 0:
                portfolio.positions.pop(code, None)

    def _sync_peaks(
        self, portfolio: Portfolio, closes: dict[str, float]
    ) -> None:
        """维护持仓期内的最高收盘价，供 ATR 移动止盈使用。"""
        held = {
            code for code, position in portfolio.positions.items() if position.shares > 0
        }
        for code in list(self._peaks):
            if code not in held:
                del self._peaks[code]
        for code in held:
            close = closes.get(code)
            if not close or close <= 0:
                continue
            previous = self._peaks.get(code)
            self._peaks[code] = close if previous is None else max(previous, close)

    def _apply_risk_controls(
        self,
        date: str,
        eligible_date: str,
        portfolio: Portfolio,
        closes: dict[str, float],
        pending: list[Order],
    ) -> list[Order]:
        """收盘后检查止损与止盈，触发则以次日开盘清仓（见 §7.3）。

        止损为固定比例（相对持仓均价）；止盈支持固定比例与 ATR 移动止盈：
        浮盈状态下，收盘价自持仓最高收盘价回撤 `atr_take_profit × ATR` 即清仓。
        0 表示关闭，已存在同票风控单时不重复生成。
        """
        stop = float(self.config.stop_loss or 0.0)
        take = float(self.config.take_profit or 0.0)
        atr_multiple = float(self.config.atr_take_profit or 0.0)
        if stop <= 0 and take <= 0 and atr_multiple <= 0:
            return pending

        waiting = {order.ts_code for order in pending if order.action == ACTION_STOP}
        ttl = max(1, int(self.config.execution.signal_ttl_days))
        for code, position in portfolio.positions.items():
            if position.shares <= 0 or position.avg_cost <= 0 or code in waiting:
                continue
            close = closes.get(code)
            if not close or close <= 0:
                continue
            pnl = close / position.avg_cost - 1.0
            note = ""
            if stop > 0 and pnl <= -stop:
                note = EXIT_STOP_LOSS
            elif take > 0 and pnl >= take:
                note = EXIT_TAKE_PROFIT
            elif atr_multiple > 0 and pnl > 0:
                peak = self._peaks.get(code)
                atr = self.panel.atr(date, code, int(self.config.atr_window))
                if peak is not None and atr and atr > 0:
                    if close <= peak - atr_multiple * atr:
                        note = EXIT_ATR_TRAIL
            if not note:
                continue
            waiting.add(code)
            pending.append(
                Order(
                    ts_code=code,
                    signal_date=date,
                    action=ACTION_STOP,
                    target_weight=0.0,
                    eligible_date=eligible_date,
                    ttl_days=ttl,
                    note=note,
                )
            )
        return pending

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

    def _cooldown_key(self, date: str) -> str:
        """返回当前日期所属的冷却周期标识（自然月或 ISO 周）。"""
        if self.config.stop_cooldown == COOLDOWN_MONTH:
            return date[:6]
        week = datetime.strptime(date, "%Y%m%d").isocalendar()
        return f"{week[0]}-W{week[1]:02d}"

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
            if order.action == ACTION_STOP:
                # 风控单优先于调仓单，保留到成交或过期
                kept.append(order)
                kept_codes.add(order.ts_code)
                continue
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

        if self.config.stop_cooldown != COOLDOWN_NONE and target:
            # 止损出局后，同一冷却周期内不再接收该标的的买入信号
            bucket = self._cooldown_key(signal_date)
            held = {
                code
                for code, position in portfolio.positions.items()
                if position.shares > 0
            }
            blocked = {
                code
                for code, key in self._cooldown.items()
                if key == bucket and code in target and code not in held
            }
            if blocked:
                self._cooldown_blocks += len(blocked)
                target = {
                    code: weight
                    for code, weight in target.items()
                    if code not in blocked
                }

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
        bars: dict[str, pd.Series | None] = {}
        sells: list[Order] = []
        buys: list[Order] = []

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
            bars[code] = bar
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
            if self._is_reduce(order, float(bar["open"]), equity_open, portfolio):
                sells.append(order)
            else:
                buys.append(order)

        if equity_open is None:
            return remaining

        # 第一阶段：卖出/减仓，先回笼现金（见 §7.1 现金约束）
        for order in sells:
            bar = bars[order.ts_code]
            name = None if bar is None else bar.get("name")
            open_price = float(bar["open"])
            if order.action in ("sell_all", ACTION_STOP):
                keep = self._handle_sell_all(
                    order, bar, name, date, open_price, portfolio, trades
                )
                # 风控单遇跌停/停牌时在有效期内顺延，避免止损被一次拒单吞掉
                if keep or (
                    order.action == ACTION_STOP
                    and order.attempts < max(1, order.ttl_days)
                ):
                    remaining.append(order)
                continue
            if self._handle_target(
                order, bar, name, date, open_price, equity_open, portfolio, trades
            ):
                remaining.append(order)

        # 第二阶段：买入按可用现金统一缩放，避免尾单被逐笔拒单
        scale = self._buy_scale(buys, equity_open, bars, portfolio)
        for order in buys:
            bar = bars[order.ts_code]
            name = None if bar is None else bar.get("name")
            open_price = float(bar["open"])
            if self._handle_target(
                order,
                bar,
                name,
                date,
                open_price,
                equity_open,
                portfolio,
                trades,
                scale=scale,
            ):
                remaining.append(order)

        return remaining

    @staticmethod
    def _is_reduce(
        order: Order,
        open_price: float,
        equity_open: float,
        portfolio: Portfolio,
    ) -> bool:
        """判断订单是否为卖出/减仓，用于排到买入之前执行。"""
        if order.action in ("sell_all", ACTION_STOP):
            return True
        position = portfolio.positions.get(order.ts_code)
        current = position.shares if position else 0
        if current <= 0 or open_price <= 0:
            return False
        target_shares = constraints.round_lot(
            equity_open * order.target_weight / open_price
        )
        return target_shares < current

    def _buy_scale(
        self,
        orders: list[Order],
        equity_open: float,
        bars: dict[str, pd.Series | None],
        portfolio: Portfolio,
    ) -> float:
        """目标名义额超过可用现金时按比例统一缩放（见 §7.1 Σ名义额 ≤ 可用现金）。"""
        if not orders or equity_open <= 0:
            return 1.0
        rate = self.config.fees.commission_rate + self.config.fees.transfer_fee
        investable = max(0.0, portfolio.cash) / (1.0 + rate)
        need = 0.0
        for order in orders:
            bar = bars.get(order.ts_code)
            if bar is None:
                continue
            open_price = float(bar["open"])
            if open_price <= 0:
                continue
            price = self.broker.fill_price(open_price, ACTION_BUY)
            position = portfolio.positions.get(order.ts_code)
            current = position.shares if position else 0
            target_shares = constraints.round_lot(
                equity_open * order.target_weight / open_price
            )
            need += max(0, target_shares - current) * price
        if need <= investable or need <= 0:
            return 1.0
        return investable / need

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
        *,
        scale: float = 1.0,
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
            if scale < 1.0:
                wanted = constraints.round_lot(wanted * scale)
            affordable = self.broker.max_affordable_shares(portfolio.cash, price)
            shares = min(wanted, affordable)
            if shares < constraints.LOT_SIZE:
                if scale >= 1.0:
                    trades.append(
                        self._reject(
                            order, date, ACTION_BUY, REASON_INSUFFICIENT_CASH, wanted
                        )
                    )
                return False
            self._fill(order, date, ACTION_BUY, shares, price, open_price, portfolio, trades)
            # 现金不足导致的缩量在有效期内顺延，下一交易日继续补足
            return shares < wanted and order.attempts < max(1, order.ttl_days)

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
                reason=order.note,
            )
        )
        if (
            action == ACTION_SELL
            and order.action == ACTION_STOP
            and order.note == EXIT_STOP_LOSS
            and self.config.stop_cooldown != COOLDOWN_NONE
        ):
            self._cooldown[order.ts_code] = self._cooldown_key(date)

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
            note=order.note,
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
        if order.action in ("sell_all", ACTION_STOP):
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
