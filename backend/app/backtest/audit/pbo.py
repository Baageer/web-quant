"""PBO（Probability of Backtest Overfitting，见 docs/BACKTEST_DESIGN.md §9.3）。

用 CSCV（组合对称交叉验证）：把收益序列切成若干区块，枚举一半区块做样本内、
另一半做样本外；每次取样本内最优参数，统计其样本外排名低于中位数的比例。
"""

from __future__ import annotations

import itertools
import math

import pandas as pd

from app.backtest.audit.stats import block_indices, sharpe_table


def probability_of_backtest_overfitting(
    returns: pd.DataFrame,
    *,
    n_blocks: int = 8,
) -> dict[str, float | int]:
    if returns is None or returns.empty or returns.shape[1] < 2:
        return {"pbo": 1.0, "combinations": 0, "configs": 0, "blocks": 0}

    blocks = _even_blocks(len(returns), n_blocks)
    if blocks < 2:
        return {"pbo": 1.0, "combinations": 0, "configs": int(returns.shape[1]), "blocks": 0}

    slices = block_indices(len(returns), blocks)
    half = blocks // 2
    below = 0
    total = 0
    for combination in itertools.combinations(range(blocks), half):
        in_sample = combination
        out_sample = tuple(index for index in range(blocks) if index not in combination)
        is_frame = pd.concat([returns.iloc[slices[index]] for index in in_sample])
        oos_frame = pd.concat([returns.iloc[slices[index]] for index in out_sample])
        is_sharpe = sharpe_table(is_frame)
        oos_sharpe = sharpe_table(oos_frame)
        if is_sharpe.empty or oos_sharpe.empty:
            continue
        best = is_sharpe.idxmax()
        best_value = float(oos_sharpe[best])
        rank = int((oos_sharpe < best_value).sum())
        rank += 0.5 * int((oos_sharpe == best_value).sum())
        percentile = rank / max(len(oos_sharpe), 1)
        if percentile <= 0.5:
            below += 1
        total += 1

    pbo = below / total if total else 1.0
    return {
        "pbo": round(pbo, 6),
        "combinations": total,
        "configs": int(returns.shape[1]),
        "blocks": blocks,
    }


def _even_blocks(rows: int, requested: int) -> int:
    blocks = min(int(requested), max(rows, 2))
    if blocks % 2:
        blocks -= 1
    return max(blocks, 0)


# 便于外部按需引用
def is_overfit(pbo: float, threshold: float = 0.5) -> bool:
    return math.isnan(pbo) or pbo > threshold
