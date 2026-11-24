"""趋势评分策略（复现线上信号口径，见 docs/BACKTEST_DESIGN.md §6.2 / §12 P4.1）。

规则：
  - 复用 `app.domain.trend_score` 的日线 + 周线共振评分，与 SignalService 同源；
  - 信号价使用 hfq 收盘价（与线上一致），成交与估值仍由引擎使用原始价；
  - 评分 >= min_score 的标的按评分降序取前 max_positions，等权持有。

只读取 BarContext（<= date 的数据），不接触未来数据。
"""

from __future__ import annotations

import pandas as pd

from app.backtest.data_loader import BarContext, Panel
from app.domain.trend_score import (
    WEEKLY_SKIP,
    component_scores,
    relative_strength_map,
    weighted_score,
)
from app.services.kline_service import resample_bars

DEFAULT_PARAMS = {
    "min_score": 30.0,
    "max_positions": 20,
    "max_position": 0.10,
    "min_history": 60,
    "window": 120,
    "daily_weight": 0.70,
    "weekly_weight": 0.30,
}

_HFQ_COLUMNS = (
    ("open", "open_hfq"),
    ("high", "high_hfq"),
    ("low", "low_hfq"),
    ("close", "close_hfq"),
    ("pre_close", "pre_close_hfq"),
)


def _hfq_view(frame: pd.DataFrame) -> pd.DataFrame:
    """把原始价列替换为后复权列，供评分使用。"""
    out = frame.copy()
    for raw, adjusted in _HFQ_COLUMNS:
        if adjusted in out.columns:
            out[raw] = out[adjusted]
    return out


class SignalRuleStrategy:
    name = "signal_rule"
    requires_flow = False

    def __init__(self) -> None:
        self.params: dict = dict(DEFAULT_PARAMS)

    def prepare(self, panel: Panel, params: dict) -> None:
        merged = dict(DEFAULT_PARAMS)
        merged.update({key: value for key, value in (params or {}).items() if value is not None})
        self.params = merged
        self.params["min_score"] = float(merged["min_score"])
        self.params["max_positions"] = max(1, int(merged["max_positions"]))
        self.params["max_position"] = float(merged["max_position"])
        self.params["min_history"] = max(21, int(merged["min_history"]))
        self.params["window"] = max(self.params["min_history"] + 21, int(merged["window"]))
        daily_weight = float(merged["daily_weight"])
        weekly_weight = float(merged["weekly_weight"])
        total = daily_weight + weekly_weight
        self.params["daily_weight"] = daily_weight / total if total > 0 else 0.70
        self.params["weekly_weight"] = weekly_weight / total if total > 0 else 0.30

    def signal(self, date: str, ctx: BarContext) -> dict[str, float]:
        min_score = float(self.params["min_score"])
        min_history = int(self.params["min_history"])
        limit = int(self.params["max_positions"])
        cap = float(self.params["max_position"])
        daily_weight = float(self.params["daily_weight"])
        weekly_weight = float(self.params["weekly_weight"])

        history = ctx.history_window(int(self.params["window"]))
        if history.empty:
            return {}
        frame = _hfq_view(history)
        groups = {
            str(code): group.sort_values("trade_date")
            for code, group in frame.groupby("ts_code", sort=False)
        }

        # 相对强度与线上一致：全市场 20 日收益的横截面百分位
        returns: dict[str, float] = {}
        for code, group in groups.items():
            closes = group["close"].to_numpy(dtype="float64")
            if closes.size >= 21 and closes[-21] > 0 and closes[-1] > 0:
                returns[code] = float(closes[-1] / closes[-21] - 1.0)
        rs_map = relative_strength_map(returns)

        candidates: list[tuple[str, float]] = []
        for code in ctx.symbols:
            group = groups.get(code)
            if group is None or len(group) < min_history:
                continue
            if str(group["trade_date"].iloc[-1]) != date:
                continue  # 当日停牌不产生新信号
            if not ctx.is_tradable(code):
                continue

            weekly = resample_bars(group, "weekly")
            if weekly.empty:
                continue

            rs_pair = rs_map.get(code)
            rs_component = (
                (rs_pair[0], f"20 日相对强度处于横截面 {rs_pair[1]:.0f}% 分位")
                if rs_pair
                else None
            )
            daily_score, _ = weighted_score(component_scores(group, rs_component))
            weekly_score, _ = weighted_score(
                component_scores(weekly, None), skip=WEEKLY_SKIP
            )
            total = daily_weight * daily_score + weekly_weight * weekly_score
            if total < min_score:
                continue
            candidates.append((code, total))

        if not candidates:
            return {}
        candidates.sort(key=lambda item: item[1], reverse=True)
        picked = candidates[:limit]
        weight = min(cap, 1.0 / len(picked))
        return {code: weight for code, _ in picked}
