"""策略注册表。"""

from __future__ import annotations

from app.core.errors import InvalidParamError
from app.backtest.strategies.base import Strategy
from app.backtest.strategies.ma_cross import MaCrossStrategy
from app.backtest.strategies.multi_factor import MultiFactorStrategy
from app.backtest.strategies.radar_topk import RadarTopKStrategy
from app.backtest.strategies.signal_rule import SignalRuleStrategy

STRATEGIES: dict[str, type] = {
    "ma_cross": MaCrossStrategy,
    "multi_factor": MultiFactorStrategy,
    "radar_topk": RadarTopKStrategy,
    "signal_rule": SignalRuleStrategy,
}


def build_strategy(name: str) -> Strategy:
    key = (name or "").strip().lower()
    if key not in STRATEGIES:
        raise InvalidParamError(
            f"未知策略：{name}，可选 {', '.join(sorted(STRATEGIES))}",
            detail={"strategy": name},
        )
    return STRATEGIES[key]()
