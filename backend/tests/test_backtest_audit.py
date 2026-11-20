from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path

import pandas as pd
import pytest

from app.api.deps import get_services
from app.backtest.audit.trials import TrialLedger
from app.backtest.audit.walk_forward import run_walk_forward, split_windows, stitch_nav
from app.backtest.metrics import compute_metrics
from app.backtest.result_store import ResultStore
from app.core.config import get_settings
from app.models.schemas import BacktestRequest
from app.services.backtest_service import BacktestService


def test_split_windows_rolls_forward():
    windows = split_windows(
        "20260101", "20261231", train_years=0.25, valid_months=3, step_months=3
    )
    assert [window["valid_start"] for window in windows] == [
        "20260401",
        "20260701",
        "20261001",
    ]
    assert windows[0]["train_start"] == "20260101"
    assert windows[0]["train_end"] == "20260331"
    assert windows[-1]["valid_end"] == "20261231"


def test_trial_ledger_is_idempotent(tmp_path):
    ledger = TrialLedger(tmp_path / "trials.jsonl")
    first = ledger.record(
        run_id="bt_1",
        strategy="ma_cross",
        params_hash="abc",
        window="20260101~20260630",
        metrics={"total_return": 0.1, "sharpe": 1.2},
    )
    second = ledger.record(
        run_id="bt_2",
        strategy="ma_cross",
        params_hash="abc",
        window="20260101~20260630",
        metrics={"total_return": 0.2, "sharpe": 1.5},
    )
    third = ledger.record(
        run_id="bt_3",
        strategy="ma_cross",
        params_hash="abc",
        window="20260701~20261231",
        metrics={"total_return": 0.3},
    )

    assert first is not None and second is None and third is not None
    lines = (tmp_path / "trials.jsonl").read_text(encoding="utf-8").strip().splitlines()
    assert len(lines) == 2
    assert json.loads(lines[0])["run_id"] == "bt_1"
    assert TrialLedger(tmp_path / "trials.jsonl").count() == 2


def test_service_records_single_trial_for_same_request(tmp_path):
    services = get_services()
    service = BacktestService(
        get_settings(),
        services.repository,
        services.calendar,
        ResultStore(tmp_path / "backtest"),
    )
    request = BacktestRequest(
        start_date="2026-04-01",
        end_date="2026-06-30",
        strategy="ma_cross",
        params={"fast": 3, "slow": 10},
        max_positions=5,
        max_position=0.2,
    )

    first = service.run(request)
    second = service.run(request)

    assert first.ok and second.ok
    assert service.trials.count() == 1
    assert any("未重复记账" in item for item in second.warnings)
    path = Path(service.store.root) / "trials.jsonl"
    assert len(path.read_text(encoding="utf-8").strip().splitlines()) == 1


def test_metrics_include_benchmark_and_full_risk_set():
    nav = pd.DataFrame(
        {
            "date": ["20260101", "20260102", "20260105", "20260106", "20260107"],
            "portfolio_value": [1_000_000.0, 1_020_000.0, 1_009_800.0, 1_040_094.0, 1_050_494.94],
            "nav": [1.0, 1.02, 1.0098, 1.040094, 1.05049494],
            "cash": [0.0] * 5,
            "cash_ratio": [0.0] * 5,
            "daily_return": [0.0, 0.02, -0.01, 0.03, 0.01],
            "drawdown": [0.0, 0.0, -0.01, 0.0, 0.0],
        }
    )
    benchmark = pd.DataFrame(
        {
            "date": ["20260101", "20260102", "20260105", "20260106", "20260107"],
            "close": [100.0, 101.0, 100.495, 102.002, 102.512],
        }
    )
    metrics = compute_metrics(
        nav,
        pd.DataFrame(),
        initial_cash=1_000_000.0,
        benchmark=benchmark,
        data_quality={"suspension_deferrals": 2, "adj_factor_missing_rate": 0.01},
    )

    for key in (
        "sortino",
        "var_95",
        "cvar_95",
        "profit_loss_ratio",
        "avg_holding_days",
        "fee_ratio",
        "benchmark_return",
        "excess_return",
        "tracking_error",
        "information_ratio",
        "alpha",
        "beta",
    ):
        assert key in metrics
    assert metrics["suspension_deferrals"] == 2
    assert metrics["adj_factor_missing_rate"] == pytest.approx(0.01)
    assert metrics["beta"] == pytest.approx(2.0, abs=0.05)
    assert metrics["benchmark_return"] == pytest.approx(0.02512, abs=1e-4)


def test_stitch_nav_compounds_folds():
    first = pd.DataFrame(
        {
            "date": ["20260105", "20260106"],
            "nav": [1.0, 1.1],
            "portfolio_value": [100.0, 110.0],
        }
    )
    second = pd.DataFrame(
        {
            "date": ["20260205", "20260206"],
            "nav": [1.0, 0.9],
            "portfolio_value": [100.0, 90.0],
        }
    )
    stitched = stitch_nav([first, second])

    assert list(stitched["date"]) == ["20260105", "20260106", "20260205", "20260206"]
    assert stitched["nav"].iloc[-1] == pytest.approx(0.99)
    assert stitched["drawdown"].iloc[-1] == pytest.approx(0.99 / 1.1 - 1.0)


def test_walk_forward_outputs_folds_and_oos_nav():
    services = get_services()
    result = run_walk_forward(
        repository=services.repository,
        calendar=services.calendar,
        strategy_name="ma_cross",
        param_grid={"fast": [5], "slow": [20]},
        start_date="20260401",
        end_date="20260630",
        train_years=0.15,
        valid_months=1,
        step_months=1,
        base_config={"initial_cash": 1_000_000.0, "max_positions": 5},
    )

    assert result["folds"], "样本区间内应至少切分出一折"
    fold = result["folds"][0]
    assert set(fold["train"]["params"]) == {"fast", "slow"}
    assert "total_return" in fold["valid"]["metrics"]
    assert not result["oos_nav"].empty
    assert {"date", "nav", "daily_return", "drawdown"} <= set(result["oos_nav"].columns)
