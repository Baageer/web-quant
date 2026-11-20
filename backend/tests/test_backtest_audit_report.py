from __future__ import annotations

import json
import time
from pathlib import Path

import numpy as np
import pandas as pd
import pytest
from fastapi.testclient import TestClient

from app.api.deps import get_services
from app.backtest.audit.cpcv import cpcv_splits, evaluate_cpcv
from app.backtest.audit.dsr import deflated_sharpe_ratio
from app.backtest.audit.pbo import probability_of_backtest_overfitting
from app.backtest.result_store import ResultStore
from app.core.config import get_settings
from app.main import app
from app.services.backtest_service import BacktestService


def _returns_frame(good: float, bad: float, rows: int = 64) -> pd.DataFrame:
    wave = np.sin(np.arange(rows) / 3.0) * 0.001
    return pd.DataFrame(
        {
            "good": good + wave,
            "bad": bad + wave,
        },
        index=[f"2026{index:04d}" for index in range(rows)],
    )


def test_pbo_detects_stable_dominant_config():
    report = probability_of_backtest_overfitting(_returns_frame(0.002, -0.001), n_blocks=8)
    assert report["pbo"] == pytest.approx(0.0)
    assert report["configs"] == 2
    assert report["combinations"] > 0


def test_cpcv_purges_neighbouring_blocks_and_evaluates():
    splits = cpcv_splits(64, n_blocks=4, n_test_blocks=1, purge_blocks=1)
    first = splits[0]
    assert first["test"] == [0]
    assert first["train"] == [2, 3]

    report = evaluate_cpcv(
        _returns_frame(0.002, -0.001), splits, n_blocks=4
    )
    assert report["splits"] == len(splits)
    assert report["positive_ratio"] == pytest.approx(1.0)
    assert report["mean_test_sharpe"] > 0


def test_dsr_penalises_many_trials():
    confident = deflated_sharpe_ratio(
        2.0, [2.0, 1.8, 1.5, 0.5], periods=252
    )
    weak = deflated_sharpe_ratio(-1.0, [2.0, 1.8, 1.5, 0.5], periods=252)

    assert 0.0 <= confident["dsr"] <= 1.0
    assert confident["trials"] == 4
    assert confident["dsr"] > weak["dsr"]
    assert confident["haircut_sharpe"] < confident["observed_sharpe"]


@pytest.fixture()
def client() -> TestClient:
    return TestClient(app)


def _wait_for_completion(client: TestClient, run_id: str, timeout: float = 60.0) -> str:
    deadline = time.monotonic() + timeout
    status = "queued"
    while time.monotonic() < deadline:
        response = client.get(f"/api/backtest/{run_id}/status")
        status = response.json()["data"]["status"]
        if status in {"completed", "failed"}:
            return status
        time.sleep(0.1)
    raise AssertionError(f"回测任务超时未完成，最后状态：{status}")


def test_audit_endpoint_writes_audit_json(client):
    submit = client.post(
        "/api/backtest/run",
        json={
            "startDate": "2026-04-01",
            "endDate": "2026-06-30",
            "strategy": "ma_cross",
            "params": {"fast": 5, "slow": 20},
            "maxPositions": 5,
        },
    )
    assert submit.status_code == 200
    run_id = submit.json()["data"]["runId"]
    assert _wait_for_completion(client, run_id) == "completed"

    response = client.post(
        f"/api/backtest/{run_id}/audit",
        json={
            "paramGrid": {"fast": [5, 10], "slow": [20, 30]},
            "nBlocks": 4,
            "nTestBlocks": 1,
            "purgeBlocks": 0,
        },
    )
    assert response.status_code == 200
    audit = response.json()["data"]["audit"]
    assert audit["verdict"] in {"accepted", "rejected"}
    assert audit["pbo"]["configs"] == 4
    assert 0.0 <= audit["dsr"]["dsr"] <= 1.0
    assert audit["cpcv"]["splits"] > 0

    stored = client.get(f"/api/backtest/{run_id}/audit")
    assert stored.status_code == 200
    assert stored.json()["data"]["audit"]["verdict"] == audit["verdict"]

    detail = client.get(f"/api/backtest/{run_id}").json()["data"]
    assert detail["manifest"]["verdict"] == audit["verdict"]
    assert detail["manifest"]["audited_at"]


def test_audit_requires_completed_run(tmp_path):
    services = get_services()
    service = BacktestService(
        get_settings(),
        services.repository,
        services.calendar,
        ResultStore(tmp_path / "backtest"),
    )
    with pytest.raises(Exception):
        service.audit_run("bt_missing")

    # 手工写入一个 running 状态，审计应直接拒绝
    run_dir = Path(service.store.root) / "bt_running"
    run_dir.mkdir(parents=True)
    (run_dir / "manifest.json").write_text(
        json.dumps({"run_id": "bt_running", "status": "running"}), encoding="utf-8"
    )
    with pytest.raises(Exception):
        service.audit_run("bt_running")
