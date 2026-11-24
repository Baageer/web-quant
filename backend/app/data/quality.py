"""数据集完整性校验。

校验结果写入 manifest，决定接口能否把本地数据当作可用数据返回。
"""

from __future__ import annotations

import pandas as pd

# 每个数据集的最小必需列
REQUIRED_COLUMNS: dict[str, tuple[str, ...]] = {
    "daily": (
        "ts_code",
        "trade_date",
        "open",
        "high",
        "low",
        "close",
        "pre_close",
        "pct_chg",
        "vol",
        "amount",
    ),
    "daily_basic": ("ts_code", "trade_date", "turnover_rate", "volume_ratio"),
    "adj_factor": ("ts_code", "trade_date", "adj_factor"),
    "index_daily": ("ts_code", "trade_date", "close", "pct_chg"),
    "moneyflow": ("ts_code", "trade_date", "net_mf_amount"),
    "limit_list": ("ts_code", "trade_date", "limit_type"),
    "suspend": ("ts_code", "trade_date", "suspend_type"),
    "fina_indicator": ("ts_code", "ann_date", "end_date"),
}

# 行数下限，低于该值视为明显不完整
MIN_ROWS: dict[str, int] = {
    "daily": 3000,
    "daily_basic": 3000,
    "adj_factor": 3000,
    "index_daily": 3,
}

# 2018 年前 A 股上市公司不足 3000 家（2017 年才首次站上 3000），全市场下限按早期规模校验
EARLY_MARKET_CUTOFF = "20180101"
EARLY_MIN_ROWS: dict[str, int] = {
    "daily": 2000,
    "daily_basic": 1500,
    "adj_factor": 2000,
}


def _min_rows(dataset: str, trade_date: str | None) -> int | None:
    floor = MIN_ROWS.get(dataset)
    if floor is None:
        return None
    if trade_date is not None:
        day = str(trade_date).replace("-", "")
        early = EARLY_MIN_ROWS.get(dataset)
        if early is not None and day < EARLY_MARKET_CUTOFF:
            return early
    return floor


def validate_dataset(
    dataset: str,
    frame: pd.DataFrame,
    trade_date: str | None = None,
    *,
    allow_empty: bool = False,
    allow_small: bool = False,
) -> tuple[bool, str | None]:
    """返回 (complete, reason)。

    allow_small 用于离线样本：样本只覆盖少量股票，不适用全市场行数下限。
    """

    if frame is None or frame.empty:
        if allow_empty:
            return True, "empty_ok"
        return False, "empty"

    missing = [col for col in REQUIRED_COLUMNS.get(dataset, ()) if col not in frame.columns]
    if missing:
        return False, f"missing_columns:{','.join(missing)}"

    if trade_date is not None and "trade_date" in frame.columns:
        dates = set(frame["trade_date"].astype(str).unique())
        if dates and dates != {trade_date}:
            return False, f"trade_date_mismatch:{','.join(sorted(dates))}"

    min_rows = _min_rows(dataset, trade_date)
    if min_rows is not None and len(frame) < min_rows and not allow_empty and not allow_small:
        return False, f"too_few_rows:{len(frame)}<{min_rows}"

    if dataset == "daily":
        if (frame["close"].astype(float) <= 0).any():
            return False, "non_positive_close"
        if (frame["high"].astype(float) < frame["low"].astype(float)).any():
            return False, "high_below_low"

    if dataset == "adj_factor":
        coverage = frame["adj_factor"].notna().mean()
        if coverage < 0.99:
            return False, f"adj_factor_coverage:{coverage:.3f}"

    return True, None
