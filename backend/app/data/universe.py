"""A 股股票池与交易规则辅助。"""

from __future__ import annotations

import pandas as pd

ST_KEYWORDS = ("ST", "*ST", "退")


def is_st_name(name: str | None) -> bool:
    text = str(name or "").upper()
    return any(keyword in text for keyword in ST_KEYWORDS)


def board_of(ts_code: str) -> str:
    symbol = ts_code.split(".", 1)[0]
    if symbol.startswith(("688", "689")):
        return "star"
    if symbol.startswith(("300", "301")):
        return "chinext"
    if symbol.startswith(("8", "4", "92")):
        return "bse"
    return "main"


def limit_threshold(ts_code: str, name: str | None = None) -> float:
    """返回涨跌停幅度（小数）。"""
    if is_st_name(name):
        return 0.05
    board = board_of(ts_code)
    if board in {"star", "chinext"}:
        return 0.20
    if board == "bse":
        return 0.30
    return 0.10


def build_universe(
    bars: pd.DataFrame,
    daily_basic: pd.DataFrame | None,
    stock_basic: pd.DataFrame | None,
    *,
    min_amount: float = 50_000_000.0,
    min_list_days: int = 60,
    trading_age: dict[str, int] | None = None,
    exclude_st: bool = True,
) -> pd.DataFrame:
    """合并横截面数据并过滤不可交易/不适合研究的标的。"""

    if bars.empty:
        return bars.copy()

    frame = bars.copy()
    if daily_basic is not None and not daily_basic.empty:
        frame = frame.merge(
            daily_basic,
            on=["ts_code", "trade_date"],
            how="left",
            suffixes=("", "_basic"),
        )
    if stock_basic is not None and not stock_basic.empty:
        columns = [
            col
            for col in ("ts_code", "name", "industry", "market", "list_date", "list_status")
            if col in stock_basic.columns
        ]
        frame = frame.merge(stock_basic[columns], on="ts_code", how="left")

    if exclude_st and "name" in frame.columns:
        frame = frame[~frame["name"].map(is_st_name)]

    if trading_age is not None and "ts_code" in frame.columns:
        ages = frame["ts_code"].map(trading_age).fillna(0)
        frame = frame[ages >= min_list_days]

    if "amount" in frame.columns:
        frame = frame[frame["amount"].astype(float) * 1000.0 >= min_amount]

    return frame.reset_index(drop=True)
