"""参数敏感性审计：运行参数网格并给出 CPCV / PBO / DSR 与 verdict。"""

from __future__ import annotations

import itertools
import json
from dataclasses import replace
from datetime import datetime
from typing import Any, Iterable

import pandas as pd

from app.data.calendar import TradeCalendar
from app.data.repository import Repository
from app.backtest.audit.cpcv import run_cpcv
from app.backtest.audit.dsr import deflated_sharpe_ratio
from app.backtest.audit.pbo import probability_of_backtest_overfitting
from app.backtest.audit.stats import annualized_sharpe
from app.backtest.data_loader import load_panel
from app.backtest.engine import BacktestEngine
from app.backtest.metrics import compute_metrics
from app.backtest.models import (
    BacktestConfig,
    ExecutionConfig,
    FeeConfig,
    UniverseConfig,
)
from app.backtest.strategies import build_strategy

DEFAULT_THRESHOLDS: dict[str, float] = {
    "pbo_max": 0.5,
    "dsr_min": 0.95,
    "cpcv_positive_min": 0.5,
}


def config_from_manifest(manifest: dict[str, Any]) -> BacktestConfig:
    """从 manifest 还原回测配置（审计时复用原始参数与费用口径）。"""
    raw = dict(manifest.get("config") or {})
    symbols = raw.get("symbols")
    return BacktestConfig(
        start_date=str(manifest.get("start_date") or raw.get("start_date")),
        end_date=str(manifest.get("end_date") or raw.get("end_date")),
        strategy=str(manifest.get("strategy") or raw.get("strategy") or "ma_cross"),
        params=dict(manifest.get("params") or raw.get("params") or {}),
        initial_cash=float(manifest.get("initial_cash") or raw.get("initial_cash") or 1_000_000.0),
        symbols=tuple(str(item) for item in symbols) if symbols else None,
        max_positions=int(raw.get("max_positions", 20)),
        max_position=float(raw.get("max_position", 0.10)),
        max_industry=float(raw.get("max_industry", 0.30)),
        max_turnover=(
            float(raw["max_turnover"]) if raw.get("max_turnover") is not None else None
        ),
        rebalance=str(raw.get("rebalance", "daily")),
        fees=FeeConfig.from_dict(raw.get("fees")),
        execution=ExecutionConfig.from_dict(raw.get("execution")),
        universe=UniverseConfig.from_dict(raw.get("universe")),
    )


def run_parameter_audit(
    *,
    repository: Repository,
    calendar: TradeCalendar,
    config: BacktestConfig,
    param_grid: dict[str, Iterable[Any]] | None = None,
    thresholds: dict[str, float] | None = None,
    n_blocks: int = 8,
    n_test_blocks: int = 2,
    purge_blocks: int = 1,
    extra_trials: int = 0,
) -> dict[str, Any]:
    """在完整区间上运行参数网格，输出审计报告。"""
    candidates = _param_grid(param_grid) or [dict(config.params)]
    limits = dict(DEFAULT_THRESHOLDS)
    limits.update({key: float(value) for key, value in (thresholds or {}).items()})

    columns: dict[str, pd.Series] = {}
    trials: list[dict[str, Any]] = []
    sharpes: dict[str, float] = {}
    for params in candidates:
        label = json.dumps(params, sort_keys=True, ensure_ascii=False)
        if label in columns:
            continue
        candidate = replace(config, params=dict(params))
        panel = load_panel(repository, calendar, candidate)
        output = BacktestEngine(panel, build_strategy(candidate.strategy), candidate).run()
        metrics = compute_metrics(
            output.nav,
            output.trades,
            initial_cash=candidate.initial_cash,
            benchmark=panel.benchmark,
        )
        series = pd.Series(
            pd.to_numeric(output.nav["daily_return"], errors="coerce").fillna(0.0),
            index=output.nav["date"].astype(str),
        )
        series = series[~series.index.duplicated(keep="last")]
        columns[label] = series
        sharpes[label] = float(metrics.get("sharpe") or 0.0)
        trials.append(
            {
                "params": dict(params),
                "sharpe": metrics.get("sharpe", 0.0),
                "total_return": metrics.get("total_return", 0.0),
                "max_drawdown": metrics.get("max_drawdown", 0.0),
                "trade_count": metrics.get("trade_count", 0),
            }
        )

    if not columns:
        raise ValueError("参数网格为空，无法审计")

    returns = pd.DataFrame(columns).fillna(0.0)
    observed_label = _match_label(columns, config.params) or max(
        columns, key=lambda name: sharpes.get(name, 0.0)
    )
    observed_sharpe = annualized_sharpe(returns[observed_label])
    periods = len(returns)

    trial_sharpes = [float(item["sharpe"] or 0.0) for item in trials]
    pbo_report = probability_of_backtest_overfitting(returns, n_blocks=n_blocks)
    cpcv_report = run_cpcv(
        returns,
        n_blocks=max(2, int(n_blocks)),
        n_test_blocks=max(1, int(n_test_blocks)),
        purge_blocks=max(0, int(purge_blocks)),
    )
    dsr_report = deflated_sharpe_ratio(
        observed_sharpe,
        trial_sharpes,
        periods=periods,
        extra_trials=extra_trials,
    )

    reasons: list[str] = []
    notes: list[str] = []
    if len(columns) < 2:
        notes.append("候选参数不足 2 组，PBO 不参与判定")
    elif float(pbo_report["pbo"]) > limits["pbo_max"]:
        reasons.append(
            f"PBO {pbo_report['pbo']:.2f} 高于阈值 {limits['pbo_max']:.2f}"
        )
    if int(cpcv_report["splits"]) == 0:
        notes.append("样本长度不足，CPCV 未参与判定")
    elif float(cpcv_report["positive_ratio"]) < limits["cpcv_positive_min"]:
        reasons.append(
            f"CPCV 样本外正收益占比 {cpcv_report['positive_ratio']:.2f} "
            f"低于阈值 {limits['cpcv_positive_min']:.2f}"
        )
    if float(dsr_report["dsr"]) < limits["dsr_min"]:
        reasons.append(
            f"DSR {dsr_report['dsr']:.3f} 低于阈值 {limits['dsr_min']:.2f}"
        )

    return {
        "created_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "strategy": config.strategy,
        "start_date": config.start_date,
        "end_date": config.end_date,
        "params": dict(config.params),
        "thresholds": limits,
        "trials": {
            "count": len(trials) + max(int(extra_trials), 0),
            "configs": sorted(returns.columns.tolist()),
            "details": trials,
        },
        "observed": {
            "label": observed_label,
            "sharpe": round(float(observed_sharpe), 6),
            "trading_days": periods,
        },
        "pbo": pbo_report,
        "cpcv": cpcv_report,
        "dsr": dsr_report,
        "verdict": "accepted" if not reasons else "rejected",
        "reasons": reasons,
        "notes": notes,
    }


def _param_grid(grid: dict[str, Iterable[Any]] | None) -> list[dict[str, Any]]:
    if not grid:
        return []
    keys = list(grid)
    values = [list(grid[key]) for key in keys]
    return [dict(zip(keys, combo)) for combo in itertools.product(*values)]


def _match_label(columns: dict[str, pd.Series], params: dict[str, Any]) -> str | None:
    label = json.dumps(params, sort_keys=True, ensure_ascii=False)
    return label if label in columns else None
