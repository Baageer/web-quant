"""A 股交易约束判定（纯函数）。

规则来源：docs/BACKTEST_DESIGN.md §5.4，阈值复用 app.data.universe。
所有函数不访问存储、不修改入参，便于单测与引擎复用。
"""

from __future__ import annotations

from typing import Any

import pandas as pd

from app.data.universe import limit_threshold as _limit_threshold
from app.backtest.models import ACTION_BUY, ACTION_SELL, REASON_LIMIT_DOWN, REASON_LIMIT_UP

LOT_SIZE = 100
# 价格比较容差：涨跌停价按 2 位小数取整后，允许 1e-6 的浮点误差
PRICE_EPS = 1e-6


def limit_threshold(ts_code: str, name: str | None = None) -> float:
    """涨跌停幅度：主板 10%、创业板/科创板 20%、北交所 30%、ST 5%。"""
    return _limit_threshold(ts_code, name)


def limit_up_price(pre_close: float, ts_code: str, name: str | None = None) -> float:
    if not pre_close or pre_close <= 0:
        return 0.0
    return round(float(pre_close) * (1.0 + limit_threshold(ts_code, name)), 2)


def limit_down_price(pre_close: float, ts_code: str, name: str | None = None) -> float:
    if not pre_close or pre_close <= 0:
        return 0.0
    return round(float(pre_close) * (1.0 - limit_threshold(ts_code, name)), 2)


def at_limit_up(price: float, pre_close: float, ts_code: str, name: str | None = None) -> bool:
    limit_price = limit_up_price(pre_close, ts_code, name)
    return bool(limit_price) and float(price) >= limit_price - PRICE_EPS


def at_limit_down(
    price: float, pre_close: float, ts_code: str, name: str | None = None
) -> bool:
    limit_price = limit_down_price(pre_close, ts_code, name)
    return bool(limit_price) and float(price) <= limit_price + PRICE_EPS


def is_suspended(bar: pd.Series | dict[str, Any] | None) -> bool:
    """缺 bar、成交量为 0 或开盘价缺失都视为停牌。"""
    if bar is None:
        return True
    if isinstance(bar, pd.Series):
        volume = bar.get("vol")
        open_price = bar.get("open")
    else:
        volume = bar.get("vol")
        open_price = bar.get("open")
    if open_price is None or pd.isna(open_price) or float(open_price) <= 0:
        return True
    if volume is None or pd.isna(volume) or float(volume) <= 0:
        return True
    return False


def round_lot(shares: float, lot: int = LOT_SIZE) -> int:
    """买入按整手向下取整。"""
    if shares is None or shares <= 0:
        return 0
    return int(shares // lot) * lot


def sellable_shares(shares: int, available: int) -> int:
    """可卖数量 = min(持仓, 可用)；当日买入的股份不计入可用（T+1）。"""
    return max(0, min(int(shares), int(available)))


def check_open_bar(
    bar: pd.Series | dict[str, Any] | None,
    action: str,
    ts_code: str,
    name: str | None = None,
) -> str | None:
    """开盘时点判定：返回拒单原因，None 表示可继续按价格撮合。"""
    if is_suspended(bar):
        return "suspended"
    open_price = float(bar.get("open"))
    pre_close = bar.get("pre_close")
    if pre_close is None or pd.isna(pre_close) or float(pre_close) <= 0:
        return None
    if action == ACTION_BUY and at_limit_up(open_price, float(pre_close), ts_code, name):
        return REASON_LIMIT_UP
    if action == ACTION_SELL and at_limit_down(
        open_price, float(pre_close), ts_code, name
    ):
        return REASON_LIMIT_DOWN
    return None
