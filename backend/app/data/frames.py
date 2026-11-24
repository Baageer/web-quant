"""DataFrame 字段类型归一。"""

from __future__ import annotations

import pandas as pd

NUMERIC_COLUMNS = {
    "open",
    "high",
    "low",
    "close",
    "pre_close",
    "change",
    "pct_chg",
    "vol",
    "amount",
    "turnover_rate",
    "turnover_rate_f",
    "volume_ratio",
    "pe",
    "pe_ttm",
    "pb",
    "ps",
    "ps_ttm",
    "dv_ratio",
    "dv_ttm",
    "total_share",
    "float_share",
    "free_share",
    "total_mv",
    "circ_mv",
    "adj_factor",
    "net_mf_amount",
    "net_mf_vol",
    "buy_lg_amount",
    "sell_lg_amount",
    "limit",
    "limit_times",
    "up_stat",
    "roe",
    "roe_waa",
    "roe_dt",
    "roe_yearly",
    "roa",
    "roic",
    "grossprofit_margin",
    "netprofit_margin",
    "debt_to_assets",
    "current_ratio",
    "quick_ratio",
    "netprofit_yoy",
    "or_yoy",
}


def coerce_numeric(frame: pd.DataFrame, columns: set[str] | None = None) -> pd.DataFrame:
    if frame.empty:
        return frame
    target = columns or NUMERIC_COLUMNS
    out = frame.copy()
    for column in target & set(out.columns):
        out[column] = pd.to_numeric(out[column], errors="coerce")
    if "trade_date" in out.columns:
        out["trade_date"] = out["trade_date"].astype(str)
    return out
