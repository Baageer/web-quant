"""复权处理。

存储原始价 + adj_factor，读取时派生：
  qfq = raw * adj_factor / adj_factor_latest
  hfq = raw * adj_factor
"""

from __future__ import annotations

import pandas as pd

PRICE_COLUMNS = ("open", "high", "low", "close", "pre_close")
SUPPORTED_MODES = {"raw", "qfq", "hfq"}


def apply_adjust(
    bars: pd.DataFrame,
    factors: pd.DataFrame | None,
    mode: str = "qfq",
) -> pd.DataFrame:
    if mode not in SUPPORTED_MODES:
        raise ValueError(f"unsupported adjust mode: {mode}")
    if mode == "raw" or bars.empty:
        return bars.copy()

    frame = bars.copy()
    if "adj_factor" not in frame.columns:
        if factors is None or factors.empty:
            return frame
        frame = frame.merge(
            factors[["ts_code", "trade_date", "adj_factor"]],
            on=["ts_code", "trade_date"],
            how="left",
        )

    frame = frame.sort_values(["ts_code", "trade_date"])
    frame["adj_factor"] = frame.groupby("ts_code")["adj_factor"].ffill().bfill()
    frame["adj_factor"] = frame["adj_factor"].fillna(1.0).astype(float)

    if mode == "qfq":
        latest = frame.groupby("ts_code")["adj_factor"].transform("last")
        scale = frame["adj_factor"] / latest.replace(0, 1.0)
    else:
        scale = frame["adj_factor"]

    for column in PRICE_COLUMNS:
        if column in frame.columns:
            frame[column] = frame[column].astype(float) * scale
    return frame
