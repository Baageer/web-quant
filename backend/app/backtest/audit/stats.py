"""审计通用统计函数。"""

from __future__ import annotations

import math

import numpy as np
import pandas as pd

TRADING_DAYS = 252


def annualized_sharpe(values: pd.Series | np.ndarray, periods: int = TRADING_DAYS) -> float:
    """按日收益计算年化 Sharpe；样本不足或零波动返回 0。"""
    array = np.asarray(values, dtype="float64")
    array = array[np.isfinite(array)]
    if array.size < 2:
        return 0.0
    std = float(array.std(ddof=0))
    if std <= 0:
        return 0.0
    return float(array.mean()) / std * math.sqrt(periods)


def sharpe_table(returns: pd.DataFrame, periods: int = TRADING_DAYS) -> pd.Series:
    """对每列（每个参数组合）计算年化 Sharpe。"""
    if returns.empty:
        return pd.Series(dtype="float64")
    return returns.apply(lambda column: annualized_sharpe(column, periods))


def block_indices(rows: int, blocks: int) -> list[np.ndarray]:
    """把行索引切成 blocks 段连续区块。"""
    if rows <= 0 or blocks <= 0:
        return []
    edges = np.linspace(0, rows, blocks + 1, dtype=int)
    return [np.arange(edges[i], edges[i + 1]) for i in range(blocks)]
