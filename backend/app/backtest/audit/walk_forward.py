"""Walk-forward 分析（见 docs/BACKTEST_DESIGN.md §9.2）。

参数在训练窗口内选择（默认按 Sharpe），在紧邻的验证窗口执行；
输出每折参数、收益、最大回撤，以及拼接后的样本外净值。
"""

from __future__ import annotations

import itertools
from typing import Any, Iterable

import pandas as pd

from app.data.calendar import TradeCalendar
from app.data.repository import Repository
from app.backtest.data_loader import load_panel
from app.backtest.engine import BacktestEngine
from app.backtest.metrics import compute_metrics
from app.backtest.models import BacktestConfig
from app.backtest.strategies import build_strategy

SUMMARY_KEYS = (
    "total_return",
    "annual_return",
    "sharpe",
    "sortino",
    "max_drawdown",
    "trade_count",
    "excess_return",
    "rejection_reasons",
)


def split_windows(
    start_date: str,
    end_date: str,
    *,
    train_years: float = 1.0,
    valid_months: int = 3,
    step_months: int = 3,
) -> list[dict[str, str]]:
    """按训练/验证窗口滚动切分，返回 YYYYMMDD 区间列表（首尾均包含）。"""
    start = pd.Timestamp(_normalize(start_date))
    end = pd.Timestamp(_normalize(end_date))
    train_months = max(1, int(round(float(train_years) * 12)))
    valid_months = max(1, int(valid_months))
    step_months = max(1, int(step_months))

    windows: list[dict[str, str]] = []
    cursor = start
    while True:
        train_end = cursor + pd.DateOffset(months=train_months) - pd.Timedelta(days=1)
        valid_start = train_end + pd.Timedelta(days=1)
        valid_end = valid_start + pd.DateOffset(months=valid_months) - pd.Timedelta(days=1)
        if valid_end > end:
            break
        windows.append(
            {
                "train_start": cursor.strftime("%Y%m%d"),
                "train_end": train_end.strftime("%Y%m%d"),
                "valid_start": valid_start.strftime("%Y%m%d"),
                "valid_end": valid_end.strftime("%Y%m%d"),
            }
        )
        cursor = cursor + pd.DateOffset(months=step_months)
    return windows


def run_walk_forward(
    *,
    repository: Repository,
    calendar: TradeCalendar,
    strategy_name: str,
    param_grid: dict[str, Iterable[Any]] | None,
    start_date: str,
    end_date: str,
    train_years: float = 1.0,
    valid_months: int = 3,
    step_months: int = 3,
    base_config: dict[str, Any] | None = None,
    selection_metric: str = "sharpe",
) -> dict[str, Any]:
    """执行 walk-forward，返回 folds 与拼接后的样本外净值。"""
    windows = split_windows(
        start_date,
        end_date,
        train_years=train_years,
        valid_months=valid_months,
        step_months=step_months,
    )
    candidates = _param_grid(param_grid)
    base = dict(base_config or {})

    folds: list[dict[str, Any]] = []
    validation_frames: list[pd.DataFrame] = []
    for index, window in enumerate(windows, start=1):
        best: dict[str, Any] | None = None
        for params in candidates:
            config = _build_config(strategy_name, params, base, window["train_start"], window["train_end"])
            panel = load_panel(repository, calendar, config)
            output = BacktestEngine(panel, build_strategy(strategy_name), config).run()
            metrics = compute_metrics(
                output.nav,
                output.trades,
                initial_cash=config.initial_cash,
                benchmark=panel.benchmark,
                data_quality=_quality(output, panel),
            )
            score = float(metrics.get(selection_metric) or 0.0)
            if best is None or score > best["score"]:
                best = {
                    "score": score,
                    "params": dict(params),
                    "metrics": _summary(metrics),
                    "actual": (panel.dates[0], panel.dates[-1]),
                }
        if best is None:
            continue

        valid_config = _build_config(
            strategy_name,
            best["params"],
            base,
            window["valid_start"],
            window["valid_end"],
        )
        valid_panel = load_panel(repository, calendar, valid_config)
        valid_output = BacktestEngine(
            valid_panel, build_strategy(strategy_name), valid_config
        ).run()
        valid_metrics = compute_metrics(
            valid_output.nav,
            valid_output.trades,
            initial_cash=valid_config.initial_cash,
            benchmark=valid_panel.benchmark,
            data_quality=_quality(valid_output, valid_panel),
        )
        validation_frames.append(valid_output.nav)

        folds.append(
            {
                "fold": index,
                "train": {
                    "start": window["train_start"],
                    "end": window["train_end"],
                    "actual_start": best["actual"][0],
                    "actual_end": best["actual"][1],
                    "params": best["params"],
                    "selection_metric": selection_metric,
                    "selection_value": round(best["score"], 6),
                    "metrics": best["metrics"],
                },
                "valid": {
                    "start": window["valid_start"],
                    "end": window["valid_end"],
                    "actual_start": valid_panel.dates[0],
                    "actual_end": valid_panel.dates[-1],
                    "metrics": _summary(valid_metrics),
                },
            }
        )

    return {
        "strategy": strategy_name,
        "selection_metric": selection_metric,
        "windows": windows,
        "candidates": candidates,
        "folds": folds,
        "oos_nav": stitch_nav(validation_frames),
    }


def stitch_nav(frames: list[pd.DataFrame]) -> pd.DataFrame:
    """把各折验证期净值按累计收益拼接为连续样本外净值。"""
    columns = ["date", "nav", "portfolio_value", "daily_return", "drawdown"]
    if not frames:
        return pd.DataFrame(columns=columns)

    rows: list[dict[str, Any]] = []
    factor = 1.0
    previous = 1.0
    peak = 1.0
    for frame in frames:
        if frame is None or frame.empty:
            continue
        for row in frame.sort_values("date").itertuples():
            nav = factor * float(row.nav)
            rows.append(
                {
                    "date": str(row.date),
                    "nav": round(nav, 6),
                    "portfolio_value": round(float(row.portfolio_value) * factor, 2),
                    "daily_return": round(nav / previous - 1.0, 6) if previous else 0.0,
                    "drawdown": round(nav / max(peak, nav) - 1.0, 6) if peak else 0.0,
                }
            )
            peak = max(peak, nav)
            previous = nav
        factor = previous
    return pd.DataFrame(rows, columns=columns)


def _param_grid(grid: dict[str, Iterable[Any]] | None) -> list[dict[str, Any]]:
    if not grid:
        return [{}]
    keys = list(grid)
    values = [list(grid[key]) for key in keys]
    return [dict(zip(keys, combo)) for combo in itertools.product(*values)]


def _build_config(
    strategy_name: str,
    params: dict[str, Any],
    base: dict[str, Any],
    start_date: str,
    end_date: str,
) -> BacktestConfig:
    allowed = {
        "initial_cash",
        "symbols",
        "max_positions",
        "max_position",
        "max_industry",
        "max_turnover",
        "rebalance",
        "fees",
        "execution",
        "universe",
    }
    extra = {key: value for key, value in base.items() if key in allowed}
    return BacktestConfig(
        start_date=start_date,
        end_date=end_date,
        strategy=strategy_name,
        params=dict(params),
        **extra,
    )


def _summary(metrics: dict[str, Any]) -> dict[str, Any]:
    return {name: metrics.get(name) for name in SUMMARY_KEYS if name in metrics}


def _quality(output, panel) -> dict[str, Any]:
    return {
        "suspension_deferrals": output.deferred_count,
        "adj_factor_missing_rate": panel.adj_missing_rate,
    }


def _normalize(value: str) -> str:
    text = str(value or "").strip().replace("-", "")
    if len(text) != 8 or not text.isdigit():
        raise ValueError(f"日期必须是 YYYYMMDD：{value}")
    return f"{text[:4]}-{text[4:6]}-{text[6:]}"
