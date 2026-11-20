"""双均线交叉基线策略（见 docs/BACKTEST_DESIGN.md §6.2）。

规则：
  - 快线 > 慢线视为多头，按动量（快线/慢线 - 1）排序取前 max_positions；
  - 每个入选标的等权，权重不超过 max_position；
  - 其余标的权重为 0，由引擎负责平仓。

只读取 BarContext（<= date 的 hfq 收盘价），不接触未来数据。
"""

from __future__ import annotations

import pandas as pd

from app.backtest.data_loader import BarContext, Panel

DEFAULT_PARAMS = {
    "fast": 5,
    "slow": 20,
    "max_positions": 20,
    "max_position": 0.10,
}


class MaCrossStrategy:
    name = "ma_cross"

    def __init__(self) -> None:
        self.params: dict = dict(DEFAULT_PARAMS)

    def prepare(self, panel: Panel, params: dict) -> None:
        merged = dict(DEFAULT_PARAMS)
        merged.update({key: value for key, value in (params or {}).items() if value is not None})
        self.params = merged
        self.params["fast"] = max(2, int(merged["fast"]))
        self.params["slow"] = max(self.params["fast"] + 1, int(merged["slow"]))
        self.params["max_positions"] = max(1, int(merged["max_positions"]))
        self.params["max_position"] = float(merged["max_position"])

    def signal(self, date: str, ctx: BarContext) -> dict[str, float]:
        fast = int(self.params["fast"])
        slow = int(self.params["slow"])
        limit = int(self.params["max_positions"])
        cap = float(self.params["max_position"])

        candidates: list[tuple[str, float]] = []
        for code in ctx.symbols:
            if not ctx.is_tradable(code):
                continue
            closes: pd.Series = ctx.closes(code)
            if len(closes) < slow + 1:
                continue
            # 当日停牌不产生新信号，避免对不可交易标的反复下单
            if str(closes.index[-1]) != date:
                continue
            fast_ma = float(closes.tail(fast).mean())
            slow_ma = float(closes.tail(slow).mean())
            if slow_ma <= 0 or fast_ma <= slow_ma:
                continue
            candidates.append((code, fast_ma / slow_ma - 1.0))

        if not candidates:
            return {}
        candidates.sort(key=lambda item: item[1], reverse=True)
        picked = candidates[:limit]
        weight = min(cap, 1.0 / len(picked))
        return {code: weight for code, _ in picked}
