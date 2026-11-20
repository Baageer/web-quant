"""CPCV（组合式交叉验证，见 docs/BACKTEST_DESIGN.md §9.3）。

把样本切成若干区块，枚举测试区块组合；训练区块与测试区块之间按
`purge_blocks` 剔除相邻区块（purge + embargo），降低持仓期带来的信息泄漏。
"""

from __future__ import annotations

import itertools
from collections import Counter

import pandas as pd

from app.backtest.audit.stats import block_indices, sharpe_table


def cpcv_splits(
    rows: int,
    *,
    n_blocks: int = 8,
    n_test_blocks: int = 2,
    purge_blocks: int = 1,
) -> list[dict[str, list[int]]]:
    """返回每个组合的（训练区块, 测试区块）索引。"""
    blocks = max(2, int(n_blocks))
    test_blocks = min(max(1, int(n_test_blocks)), blocks - 1)
    purge = max(0, int(purge_blocks))
    if rows < blocks:
        return []

    splits: list[dict[str, list[int]]] = []
    for test in itertools.combinations(range(blocks), test_blocks):
        train = [
            index
            for index in range(blocks)
            if all(abs(index - hit) > purge for hit in test)
        ]
        if not train:
            continue
        splits.append({"train": train, "test": list(test)})
    return splits


def evaluate_cpcv(
    returns: pd.DataFrame,
    splits: list[dict[str, list[int]]],
    *,
    n_blocks: int,
    periods: int = 252,
    max_paths: int = 60,
) -> dict[str, object]:
    """在给定切分上选择参数并统计测试期表现。"""
    if returns is None or returns.empty or not splits:
        return {
            "splits": 0,
            "mean_test_sharpe": 0.0,
            "std_test_sharpe": 0.0,
            "positive_ratio": 0.0,
            "paths": [],
            "selected": {},
        }

    slices = block_indices(len(returns), int(n_blocks))
    paths: list[dict[str, object]] = []
    selected: Counter[str] = Counter()
    test_sharpes: list[float] = []

    for order, split in enumerate(splits, start=1):
        train_frame = pd.concat([returns.iloc[slices[index]] for index in split["train"]])
        test_frame = pd.concat([returns.iloc[slices[index]] for index in split["test"]])
        train_sharpe = sharpe_table(train_frame, periods=periods)
        test_sharpe = sharpe_table(test_frame, periods=periods)
        if train_sharpe.empty or test_sharpe.empty:
            continue
        best = str(train_sharpe.idxmax())
        value = float(test_sharpe.get(best, 0.0))
        selected[best] += 1
        test_sharpes.append(value)
        paths.append(
            {
                "split": order,
                "selected": best,
                "train_sharpe": round(float(train_sharpe[best]), 6),
                "test_sharpe": round(value, 6),
            }
        )

    if not test_sharpes:
        return {
            "splits": 0,
            "mean_test_sharpe": 0.0,
            "std_test_sharpe": 0.0,
            "positive_ratio": 0.0,
            "paths": [],
            "selected": {},
        }

    series = pd.Series(test_sharpes, dtype="float64")
    return {
        "splits": len(test_sharpes),
        "mean_test_sharpe": round(float(series.mean()), 6),
        "std_test_sharpe": round(float(series.std(ddof=0)), 6),
        "positive_ratio": round(float((series > 0).mean()), 6),
        "paths": paths[: max(1, int(max_paths))],
        "selected": dict(selected.most_common()),
    }


def run_cpcv(
    returns: pd.DataFrame,
    *,
    n_blocks: int = 8,
    n_test_blocks: int = 2,
    purge_blocks: int = 1,
) -> dict[str, object]:
    splits = cpcv_splits(
        len(returns),
        n_blocks=n_blocks,
        n_test_blocks=n_test_blocks,
        purge_blocks=purge_blocks,
    )
    return evaluate_cpcv(returns, splits, n_blocks=n_blocks)
