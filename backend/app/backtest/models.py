"""回测数据结构：配置、订单、成交与结果。

约定（见 docs/BACKTEST_DESIGN.md §5）：
  - 信号在 T 日收盘生成，订单只能在 T+1 及之后的交易日执行；
  - 订单状态只有 pending / filled / rejected / expired 四种；
  - 拒单原因与状态一同入库，不做「假设成交」。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import pandas as pd

# ---- 订单状态 ----

ORDER_PENDING = "pending"
ORDER_FILLED = "filled"
ORDER_REJECTED = "rejected"
ORDER_EXPIRED = "expired"

ORDER_STATUSES = (ORDER_PENDING, ORDER_FILLED, ORDER_REJECTED, ORDER_EXPIRED)

# ---- 拒单原因 ----

REASON_LIMIT_UP = "limit_up"
REASON_LIMIT_DOWN = "limit_down"
REASON_SUSPENDED = "suspended"
REASON_T_PLUS_ONE = "t_plus_one"
REASON_INSUFFICIENT_CASH = "insufficient_cash"
REASON_NOT_IN_UNIVERSE = "not_in_universe"
REASON_NO_POSITION = "no_position"
REASON_SIGNAL_EXPIRED = "signal_expired"
REASON_INVALID_PRICE = "invalid_price"

# ---- 订单动作 ----

ACTION_BUY = "buy"
ACTION_SELL = "sell"
ACTION_STOP = "stop_sell"  # 止盈止损触发的清仓单

# ---- 风控退出原因 ----

EXIT_STOP_LOSS = "stop_loss"
EXIT_TAKE_PROFIT = "take_profit"
EXIT_ATR_TRAIL = "atr_trailing"

# ---- 调仓频率 ----

REBALANCE_DAILY = "daily"
REBALANCE_WEEKLY = "weekly"
REBALANCE_MONTHLY = "monthly"
REBALANCE_MODES = (REBALANCE_DAILY, REBALANCE_WEEKLY, REBALANCE_MONTHLY)

# ---- 止损后重新入场冷却 ----

COOLDOWN_NONE = "none"
COOLDOWN_WEEK = "week"
COOLDOWN_MONTH = "month"
COOLDOWN_MODES = (COOLDOWN_NONE, COOLDOWN_WEEK, COOLDOWN_MONTH)

# trades.parquet 列契约（顺序即落盘顺序）
TRADE_COLUMNS = [
    "date",
    "signal_date",
    "ts_code",
    "action",
    "status",
    "rejection_reason",
    "shares",
    "price",
    "amount",
    "commission",
    "stamp_duty",
    "transfer_fee",
    "slippage_cost",
    "profit",
    "reason",
]

NAV_COLUMNS = [
    "date",
    "portfolio_value",
    "nav",
    "cash",
    "cash_ratio",
    "daily_return",
    "drawdown",
]

POSITION_COLUMNS = ["date", "ts_code", "shares", "weight", "close", "market_value"]


def _pick(data: dict[str, Any], key: str, default: Any) -> Any:
    value = data.get(key, default)
    return default if value is None else value


@dataclass(frozen=True)
class FeeConfig:
    """交易费用（见 §5.5）。"""

    commission_rate: float = 0.00025
    commission_min: float = 5.0
    stamp_duty_sell: float = 0.0005
    transfer_fee: float = 0.00001

    @classmethod
    def from_dict(cls, data: dict[str, Any] | None) -> "FeeConfig":
        data = data or {}
        base = cls()
        return cls(
            commission_rate=float(_pick(data, "commission_rate", base.commission_rate)),
            commission_min=float(_pick(data, "commission_min", base.commission_min)),
            stamp_duty_sell=float(_pick(data, "stamp_duty_sell", base.stamp_duty_sell)),
            transfer_fee=float(_pick(data, "transfer_fee", base.transfer_fee)),
        )


@dataclass(frozen=True)
class ExecutionConfig:
    """执行参数（见 §5.3 / §5.2）。"""

    signal_ttl_days: int = 1
    slippage_bps: float = 5.0

    @classmethod
    def from_dict(cls, data: dict[str, Any] | None) -> "ExecutionConfig":
        data = data or {}
        base = cls()
        return cls(
            signal_ttl_days=int(_pick(data, "signal_ttl_days", base.signal_ttl_days)),
            slippage_bps=float(_pick(data, "slippage_bps", base.slippage_bps)),
        )


@dataclass(frozen=True)
class UniverseConfig:
    """股票池过滤（见 §4.2）。"""

    exclude_st: bool = True
    min_list_days: int = 60
    min_avg_amount: float = 50_000_000.0
    avg_window: int = 20

    @classmethod
    def from_dict(cls, data: dict[str, Any] | None) -> "UniverseConfig":
        data = data or {}
        base = cls()
        return cls(
            exclude_st=bool(_pick(data, "exclude_st", base.exclude_st)),
            min_list_days=int(_pick(data, "min_list_days", base.min_list_days)),
            min_avg_amount=float(_pick(data, "min_avg_amount", base.min_avg_amount)),
            avg_window=int(_pick(data, "avg_window", base.avg_window)),
        )


@dataclass(frozen=True)
class BacktestConfig:
    start_date: str
    end_date: str
    strategy: str = "ma_cross"
    params: dict[str, Any] = field(default_factory=dict)
    initial_cash: float = 1_000_000.0
    symbols: tuple[str, ...] | None = None
    max_positions: int = 20
    max_position: float = 0.10
    max_industry: float = 0.30
    max_turnover: float | None = None
    rebalance: str = "daily"
    stop_loss: float = 0.0
    take_profit: float = 0.0
    atr_window: int = 14
    atr_take_profit: float = 0.0
    stop_cooldown: str = "none"
    fees: FeeConfig = field(default_factory=FeeConfig)
    execution: ExecutionConfig = field(default_factory=ExecutionConfig)
    universe: UniverseConfig = field(default_factory=UniverseConfig)

    def __post_init__(self) -> None:
        mode = str(self.rebalance or REBALANCE_DAILY).strip().lower()
        if mode not in REBALANCE_MODES:
            raise ValueError(
                f"未知调仓频率：{self.rebalance}，可选 {', '.join(REBALANCE_MODES)}"
            )
        object.__setattr__(self, "rebalance", mode)
        if not 0.0 <= float(self.stop_loss) < 1.0:
            raise ValueError("stop_loss 必须在 [0, 1) 区间，0 表示关闭")
        if float(self.take_profit) < 0.0:
            raise ValueError("take_profit 必须大于等于 0，0 表示关闭")
        if int(self.atr_window) < 2:
            raise ValueError("atr_window 必须大于等于 2")
        if float(self.atr_take_profit) < 0.0:
            raise ValueError("atr_take_profit 必须大于等于 0，0 表示关闭")
        cooldown = str(self.stop_cooldown or COOLDOWN_NONE).strip().lower()
        if cooldown not in COOLDOWN_MODES:
            raise ValueError(
                f"未知止损冷却周期：{self.stop_cooldown}，可选 {', '.join(COOLDOWN_MODES)}"
            )
        object.__setattr__(self, "stop_cooldown", cooldown)

    def as_dict(self) -> dict[str, Any]:
        return {
            "start_date": self.start_date,
            "end_date": self.end_date,
            "strategy": self.strategy,
            "params": dict(self.params),
            "initial_cash": self.initial_cash,
            "symbols": list(self.symbols) if self.symbols else None,
            "max_positions": self.max_positions,
            "max_position": self.max_position,
            "max_industry": self.max_industry,
            "max_turnover": self.max_turnover,
            "rebalance": self.rebalance,
            "stop_loss": self.stop_loss,
            "take_profit": self.take_profit,
            "atr_window": self.atr_window,
            "atr_take_profit": self.atr_take_profit,
            "stop_cooldown": self.stop_cooldown,
            "fees": {
                "commission_rate": self.fees.commission_rate,
                "commission_min": self.fees.commission_min,
                "stamp_duty_sell": self.fees.stamp_duty_sell,
                "transfer_fee": self.fees.transfer_fee,
            },
            "execution": {
                "signal_ttl_days": self.execution.signal_ttl_days,
                "slippage_bps": self.execution.slippage_bps,
            },
            "universe": {
                "exclude_st": self.universe.exclude_st,
                "min_list_days": self.universe.min_list_days,
                "min_avg_amount": self.universe.min_avg_amount,
                "avg_window": self.universe.avg_window,
            },
        }


@dataclass
class Order:
    """目标权重订单。

    action="target"    按 target_weight 调整到目标仓位（可买可卖）；
    action="sell_all"  清仓；
    action="stop_sell" 止盈止损触发的清仓，优先级高于同票调仓单。
    """

    ts_code: str
    signal_date: str
    action: str
    target_weight: float = 0.0
    eligible_date: str | None = None
    ttl_days: int = 1
    attempts: int = 0
    note: str = ""


@dataclass
class TradeRecord:
    date: str
    signal_date: str
    ts_code: str
    action: str
    status: str
    rejection_reason: str = ""
    shares: int = 0
    price: float = 0.0
    amount: float = 0.0
    commission: float = 0.0
    stamp_duty: float = 0.0
    transfer_fee: float = 0.0
    slippage_cost: float = 0.0
    profit: float = 0.0
    reason: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {column: getattr(self, column) for column in TRADE_COLUMNS}


@dataclass
class EngineOutput:
    nav: pd.DataFrame
    trades: pd.DataFrame
    positions: pd.DataFrame
    warnings: list[str] = field(default_factory=list)
    deferred_count: int = 0


@dataclass
class BacktestResult:
    run_id: str
    config: BacktestConfig
    nav: pd.DataFrame
    trades: pd.DataFrame
    positions: pd.DataFrame
    metrics: dict[str, Any]
    manifest: dict[str, Any]
    warnings: list[str] = field(default_factory=list)
