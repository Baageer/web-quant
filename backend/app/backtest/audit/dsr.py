"""DSR（Deflated Sharpe Ratio，见 docs/BACKTEST_DESIGN.md §9.3）。

结合试验次数与试验夏普方差，输出期望最大夏普（SR0）、显著性 z 与紧缩概率。
"""

from __future__ import annotations

import math
from statistics import NormalDist
from typing import Iterable

import numpy as np

from app.backtest.audit.stats import annualized_sharpe

EULER_GAMMA = 0.5772156649015329
NORMAL = NormalDist()


def deflated_sharpe_ratio(
    observed_sharpe: float,
    trial_sharpes: Iterable[float],
    *,
    periods: int,
    skew: float = 0.0,
    kurtosis: float = 3.0,
    extra_trials: int = 0,
) -> dict[str, float | int]:
    """Bailey & López de Prado 的 DSR。

    `observed_sharpe` 与 `trial_sharpes` 均为年化值，计算前换算为周期值。
    """
    trials = [float(value) for value in trial_sharpes if np.isfinite(value)]
    count = max(len(trials), 1) + max(int(extra_trials), 0)
    periods = max(int(periods), 2)

    sr = float(observed_sharpe) / math.sqrt(252)
    trial_period_sr = np.asarray(trials, dtype="float64") / math.sqrt(252)
    variance = float(trial_period_sr.var(ddof=1)) if trial_period_sr.size > 1 else 0.0

    if count > 1:
        z1 = NORMAL.inv_cdf(1.0 - 1.0 / count)
        z2 = NORMAL.inv_cdf(1.0 - 1.0 / (count * math.e))
        sr0 = math.sqrt(variance) * ((1.0 - EULER_GAMMA) * z1 + EULER_GAMMA * z2)
    else:
        sr0 = 0.0

    denominator = math.sqrt(
        max(1.0 - skew * sr + (kurtosis - 1.0) / 4.0 * sr * sr, 1e-12)
    )
    z = (sr - sr0) * math.sqrt(max(periods - 1, 1)) / denominator
    dsr = float(NORMAL.cdf(z))

    return {
        "dsr": round(dsr, 6),
        "z": round(z, 6),
        "sr0": round(sr0 * math.sqrt(252), 6),
        "haircut_sharpe": round((sr - sr0) * math.sqrt(252), 6),
        "observed_sharpe": round(float(observed_sharpe), 6),
        "trials": count,
    }


# 供外部复用：从日收益直接得到年化 Sharpe
sharpe = annualized_sharpe
