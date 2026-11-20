"""绩效指标（见 docs/BACKTEST_DESIGN.md §8）。

P4.0 实现基础指标，P4.1 补充分年/分月指标，P4.2 补齐风险调整、交易、成本、
数据质量与基准对比指标；全部为基于 nav / trades 的纯函数，年化系数固定 252。
"""

from __future__ import annotations

import copy
import math
from datetime import datetime
from typing import Any

import numpy as np
import pandas as pd

TRADING_DAYS = 252

_EMPTY_METRICS: dict[str, Any] = {
    "total_return": 0.0,
    "annual_return": 0.0,
    "annual_volatility": 0.0,
    "sharpe": 0.0,
    "sortino": 0.0,
    "max_drawdown": 0.0,
    "max_drawdown_days": 0,
    "calmar": 0.0,
    "var_95": 0.0,
    "cvar_95": 0.0,
    "final_value": 0.0,
    "years": 0.0,
    "trade_count": 0,
    "filled_count": 0,
    "rejected_count": 0,
    "expired_count": 0,
    "win_rate": 0.0,
    "profit_factor": 0.0,
    "profit_loss_ratio": 0.0,
    "avg_holding_days": 0.0,
    "total_fees": 0.0,
    "fee_ratio": 0.0,
    "turnover_rate": 0.0,
    "rejection_reasons": {},
    "suspension_deferrals": 0,
    "adj_factor_missing_rate": 0.0,
    "benchmark_return": 0.0,
    "excess_return": 0.0,
    "tracking_error": 0.0,
    "information_ratio": 0.0,
    "alpha": 0.0,
    "beta": 0.0,
    "monthly_returns": {},
    "yearly_returns": {},
    "monthly_metrics": {},
    "yearly_metrics": {},
}


def compute_metrics(
    nav: pd.DataFrame,
    trades: pd.DataFrame,
    *,
    initial_cash: float,
    benchmark: pd.DataFrame | None = None,
    data_quality: dict[str, Any] | None = None,
) -> dict[str, Any]:
    quality = data_quality or {}
    if nav is None or nav.empty:
        empty = copy.deepcopy(_EMPTY_METRICS)
        empty["final_value"] = float(initial_cash)
        empty["suspension_deferrals"] = int(quality.get("suspension_deferrals") or 0)
        empty["adj_factor_missing_rate"] = float(
            quality.get("adj_factor_missing_rate") or 0.0
        )
        return empty

    frame = nav.sort_values("date").reset_index(drop=True)
    final_value = float(frame["portfolio_value"].iloc[-1])
    days = len(frame)
    years = days / TRADING_DAYS
    total_return = final_value / initial_cash - 1.0 if initial_cash else 0.0
    annual_return = (
        (final_value / initial_cash) ** (1.0 / years) - 1.0
        if initial_cash > 0 and years > 0 and final_value > 0
        else 0.0
    )

    returns = pd.to_numeric(frame["daily_return"], errors="coerce").fillna(0.0)
    daily_std = float(returns.std(ddof=0))
    annual_volatility = daily_std * math.sqrt(TRADING_DAYS)
    sharpe = (
        float(returns.mean()) / daily_std * math.sqrt(TRADING_DAYS)
        if daily_std > 0
        else 0.0
    )
    downside = returns[returns < 0]
    downside_std = float(downside.std(ddof=0)) if len(downside) else 0.0
    sortino = (
        float(returns.mean()) / downside_std * math.sqrt(TRADING_DAYS)
        if downside_std > 0
        else 0.0
    )

    drawdown = float(frame["drawdown"].min())
    max_drawdown_days = _max_drawdown_days(frame["drawdown"])
    calmar = annual_return / abs(drawdown) if drawdown < 0 else 0.0

    var_95, cvar_95 = _tail_risk(returns)

    stats = _trade_stats(trades)
    total_fees = stats.pop("total_fees")
    total_amount = stats.pop("total_amount")
    turnover_rate = total_amount / initial_cash / years if initial_cash > 0 and years > 0 else 0.0
    net_profit = final_value - initial_cash
    fee_ratio = total_fees / net_profit if net_profit > 0 else 0.0
    avg_holding_days = _avg_holding_days(trades)

    benchmark_metrics = _benchmark_metrics(
        frame, returns, benchmark, total_return=total_return, annual_return=annual_return
    )

    return {
        "total_return": _round(total_return),
        "annual_return": _round(annual_return),
        "annual_volatility": _round(annual_volatility),
        "sharpe": _round(sharpe),
        "sortino": _round(sortino),
        "max_drawdown": _round(drawdown),
        "max_drawdown_days": max_drawdown_days,
        "calmar": _round(calmar),
        "var_95": _round(var_95),
        "cvar_95": _round(cvar_95),
        "final_value": round(final_value, 2),
        "years": _round(years),
        "trade_count": stats["trade_count"],
        "filled_count": stats["filled_count"],
        "rejected_count": stats["rejected_count"],
        "expired_count": stats["expired_count"],
        "buy_count": stats["buy_count"],
        "sell_count": stats["sell_count"],
        "win_rate": _round(stats["win_rate"]),
        "profit_factor": _round(stats["profit_factor"]),
        "profit_loss_ratio": _round(stats["profit_loss_ratio"]),
        "avg_holding_days": _round(avg_holding_days),
        "total_fees": round(total_fees, 2),
        "fee_ratio": _round(fee_ratio),
        "turnover_rate": _round(turnover_rate),
        "rejection_reasons": stats["rejection_reasons"],
        "suspension_deferrals": int(quality.get("suspension_deferrals") or 0),
        "adj_factor_missing_rate": _round(
            float(quality.get("adj_factor_missing_rate") or 0.0)
        ),
        **benchmark_metrics,
        "monthly_returns": _period_returns(frame, "%Y-%m"),
        "yearly_returns": _period_returns(frame, "%Y"),
        "monthly_metrics": _period_metrics(frame, "%Y-%m"),
        "yearly_metrics": _period_metrics(frame, "%Y"),
    }


def _trade_stats(trades: pd.DataFrame) -> dict[str, Any]:
    columns = {
        "trade_count": 0,
        "filled_count": 0,
        "rejected_count": 0,
        "expired_count": 0,
        "buy_count": 0,
        "sell_count": 0,
        "win_rate": 0.0,
        "profit_factor": 0.0,
        "profit_loss_ratio": 0.0,
        "total_fees": 0.0,
        "total_amount": 0.0,
        "rejection_reasons": {},
    }
    if trades is None or trades.empty:
        return columns

    status = trades["status"].astype(str)
    filled = trades[status == "filled"]
    rejected = trades[status == "rejected"]
    expired = trades[status == "expired"]

    columns["trade_count"] = int(len(trades))
    columns["filled_count"] = int(len(filled))
    columns["rejected_count"] = int(len(rejected))
    columns["expired_count"] = int(len(expired))
    columns["buy_count"] = int((filled["action"] == "buy").sum())
    columns["sell_count"] = int((filled["action"] == "sell").sum())

    if not filled.empty:
        fee_total = (
            filled["commission"].astype(float)
            + filled["stamp_duty"].astype(float)
            + filled["transfer_fee"].astype(float)
        )
        columns["total_fees"] = float(fee_total.sum())
        columns["total_amount"] = float(filled["amount"].astype(float).sum())

    sells = filled[filled["action"] == "sell"] if not filled.empty else filled
    if len(sells):
        profit = pd.to_numeric(sells["profit"], errors="coerce").fillna(0.0)
        columns["win_rate"] = float((profit > 0).sum() / len(profit))
        wins = profit[profit > 0]
        losses = profit[profit < 0]
        gross_profit = float(wins.sum())
        gross_loss = abs(float(losses.sum()))
        if gross_loss > 0:
            columns["profit_factor"] = gross_profit / gross_loss
        elif gross_profit > 0:
            columns["profit_factor"] = float("inf")
        if len(losses) and len(wins):
            columns["profit_loss_ratio"] = float(wins.mean()) / abs(float(losses.mean()))

    if not rejected.empty:
        reasons = rejected["rejection_reason"].astype(str).value_counts()
        columns["rejection_reasons"] = {str(key): int(value) for key, value in reasons.items()}
    return columns


def _period_returns(frame: pd.DataFrame, pattern: str) -> dict[str, float]:
    if frame.empty:
        return {}
    dates = pd.to_datetime(frame["date"].astype(str), format="%Y%m%d", errors="coerce")
    if dates.isna().all():
        return {}
    grouped = (
        pd.DataFrame({"period": dates.dt.strftime(pattern), "nav": frame["nav"].astype(float)})
        .dropna()
        .groupby("period")["nav"]
        .last()
        .sort_index()
    )
    returns = grouped.pct_change().dropna()
    return {str(key): _round(float(value)) for key, value in returns.items()}


def _period_metrics(frame: pd.DataFrame, pattern: str) -> dict[str, dict[str, float]]:
    """分年/分月指标：区间收益、年化波动、最大回撤与 Sharpe。"""
    if frame.empty:
        return {}
    dates = pd.to_datetime(frame["date"].astype(str), format="%Y%m%d", errors="coerce")
    if dates.isna().all():
        return {}
    source = pd.DataFrame(
        {
            "period": dates.dt.strftime(pattern),
            "nav": pd.to_numeric(frame["nav"], errors="coerce"),
            "ret": pd.to_numeric(frame["daily_return"], errors="coerce").fillna(0.0),
        }
    ).dropna(subset=["period"])

    output: dict[str, dict[str, float]] = {}
    for period, group in source.groupby("period", sort=True):
        values = group["ret"].astype(float)
        days = int(len(values))
        if days == 0:
            continue
        std = float(values.std(ddof=0))
        nav_values = group["nav"].astype(float)
        peak = nav_values.cummax()
        drawdown = (nav_values / peak - 1.0).min()
        output[str(period)] = {
            "return": _round(float((1.0 + values).prod() - 1.0)),
            "volatility": _round(std * math.sqrt(TRADING_DAYS)),
            "sharpe": _round(
                float(values.mean()) / std * math.sqrt(TRADING_DAYS) if std > 0 else 0.0
            ),
            "max_drawdown": _round(float(drawdown) if pd.notna(drawdown) else 0.0),
            "days": days,
        }
    return output


def _tail_risk(returns: pd.Series) -> tuple[float, float]:
    """历史法 95% VaR / CVaR，返回正数（潜在损失幅度）。"""
    if returns.empty:
        return 0.0, 0.0
    values = returns.to_numpy(dtype=float)
    percentile = float(np.nanpercentile(values, 5))
    tail = values[values <= percentile]
    var_95 = max(-percentile, 0.0)
    cvar_95 = max(-float(np.nanmean(tail)), 0.0) if tail.size else var_95
    return var_95, cvar_95


def _parse_day(value: Any) -> datetime | None:
    text = str(value or "")[:8]
    try:
        return datetime.strptime(text, "%Y%m%d")
    except ValueError:
        return None


def _avg_holding_days(trades: pd.DataFrame | None) -> float:
    """FIFO 配对买卖成交，按股数加权计算平均持仓自然日。"""
    if trades is None or trades.empty:
        return 0.0
    filled = trades[trades["status"].astype(str) == "filled"]
    if filled.empty:
        return 0.0
    lots: dict[str, list[list[Any]]] = {}
    total_days = 0.0
    total_shares = 0
    for row in filled.sort_values(["date", "ts_code"]).itertuples():
        shares = int(row.shares or 0)
        if shares <= 0:
            continue
        day = _parse_day(row.date)
        if day is None:
            continue
        code = str(row.ts_code)
        if str(row.action) == "buy":
            lots.setdefault(code, []).append([shares, day])
            continue
        remaining = shares
        queue = lots.get(code, [])
        while remaining > 0 and queue:
            lot = queue[0]
            matched = min(remaining, int(lot[0]))
            total_days += float((day - lot[1]).days) * matched
            total_shares += matched
            lot[0] -= matched
            remaining -= matched
            if lot[0] <= 0:
                queue.pop(0)
        if remaining > 0:  # 缺少对应买入记录（区间前持仓），按 0 天计
            total_shares += remaining
    return total_days / total_shares if total_shares else 0.0


def _benchmark_metrics(
    frame: pd.DataFrame,
    returns: pd.Series,
    benchmark: pd.DataFrame | None,
    *,
    total_return: float,
    annual_return: float,
) -> dict[str, float]:
    empty = {
        "benchmark_return": 0.0,
        "excess_return": 0.0,
        "tracking_error": 0.0,
        "information_ratio": 0.0,
        "alpha": 0.0,
        "beta": 0.0,
    }
    if benchmark is None or benchmark.empty or "close" not in benchmark.columns:
        return empty

    closes = pd.DataFrame(
        {
            "date": benchmark["date"].astype(str).str.replace("-", "", regex=False).str[:8],
            "close": pd.to_numeric(benchmark["close"], errors="coerce"),
        }
    ).dropna()
    closes = closes.sort_values("date").drop_duplicates("date").set_index("date")["close"]
    aligned = closes.reindex(frame["date"].astype(str)).ffill().dropna()
    if len(aligned) < 2:
        return empty

    benchmark_total = float(aligned.iloc[-1] / aligned.iloc[0] - 1.0)
    benchmark_returns = aligned.pct_change().fillna(0.0)
    portfolio_returns = pd.Series(
        returns.to_numpy(dtype=float), index=frame["date"].astype(str)
    ).reindex(aligned.index).fillna(0.0)
    active = portfolio_returns - benchmark_returns

    active_std = float(active.std(ddof=0))
    tracking_error = active_std * math.sqrt(TRADING_DAYS)
    information_ratio = (
        float(active.mean()) / active_std * math.sqrt(TRADING_DAYS)
        if active_std > 0
        else 0.0
    )
    benchmark_var = float(benchmark_returns.var(ddof=0))
    beta = (
        float(np.cov(portfolio_returns, benchmark_returns, ddof=0)[0, 1])
        / benchmark_var
        if benchmark_var > 0
        else 0.0
    )
    years = len(portfolio_returns) / TRADING_DAYS
    benchmark_annual = (
        (1.0 + benchmark_total) ** (1.0 / years) - 1.0
        if years > 0 and benchmark_total > -1
        else 0.0
    )
    alpha = annual_return - beta * benchmark_annual

    return {
        "benchmark_return": _round(benchmark_total),
        "excess_return": _round(total_return - benchmark_total),
        "tracking_error": _round(tracking_error),
        "information_ratio": _round(information_ratio),
        "alpha": _round(alpha),
        "beta": _round(beta),
    }


def _max_drawdown_days(drawdown: pd.Series) -> int:
    longest = 0
    current = 0
    for value in pd.to_numeric(drawdown, errors="coerce").fillna(0.0):
        if value < 0:
            current += 1
            longest = max(longest, current)
        else:
            current = 0
    return int(longest)


def _round(value: float, digits: int = 6) -> float:
    number = float(value)
    if math.isnan(number) or math.isinf(number):
        return 0.0
    return round(number, digits)


def json_safe(value: Any) -> Any:
    """递归转换 numpy / pandas 标量，保证 JSON 可序列化。"""
    if isinstance(value, dict):
        return {str(key): json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [json_safe(item) for item in value]
    if isinstance(value, (np.integer,)):
        return int(value)
    if isinstance(value, (np.floating,)):
        number = float(value)
        return None if math.isnan(number) or math.isinf(number) else round(number, 6)
    if isinstance(value, np.bool_):
        return bool(value)
    if isinstance(value, float) and (math.isnan(value) or math.isinf(value)):
        return None
    if isinstance(value, pd.Timestamp):
        return value.isoformat()
    return value
