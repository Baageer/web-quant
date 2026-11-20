from __future__ import annotations

import time
from dataclasses import replace

import pytest
from fastapi.testclient import TestClient

from app.api.deps import get_services
from app.backtest.result_store import ResultStore
from app.core.config import get_settings
from app.main import app
from app.services.backtest_service import BacktestService


def _wait_for_completion(client: TestClient, run_id: str, timeout: float = 60.0) -> str:
    deadline = time.monotonic() + timeout
    status = "queued"
    while time.monotonic() < deadline:
        response = client.get(f"/api/backtest/{run_id}/status")
        assert response.status_code == 200
        status = response.json()["data"]["status"]
        if status in {"completed", "failed"}:
            return status
        time.sleep(0.1)
    raise AssertionError(f"回测任务超时未完成，最后状态：{status}")


@pytest.fixture()
def client(tmp_path):
    services = get_services()
    custom = replace(
        services,
        backtest=BacktestService(
            get_settings(),
            services.repository,
            services.calendar,
            ResultStore(tmp_path / "backtest"),
        ),
    )
    app.dependency_overrides[get_services] = lambda: custom
    try:
        yield TestClient(app)
    finally:
        app.dependency_overrides.clear()


def _run_payload(**overrides) -> dict:
    payload = {
        "startDate": "2026-04-01",
        "endDate": "2026-09-30",
        "strategy": "ma_cross",
        "params": {"fast": 3, "slow": 10},
        "initialCash": 1_000_000,
        "maxPositions": 5,
        "maxPosition": 0.2,
    }
    payload.update(overrides)
    return payload


def test_run_and_query_contract(client):
    response = client.post("/api/backtest/run", json=_run_payload())
    assert response.status_code == 200
    body = response.json()
    assert body["ok"] is True
    assert body["source"] == "backtest"

    summary = body["data"]
    assert summary["runId"].startswith("bt_")
    assert summary["status"] in {"queued", "running", "completed"}
    assert summary["strategy"] == "ma_cross"

    run_id = summary["runId"]
    assert _wait_for_completion(client, run_id) == "completed"

    status = client.get(f"/api/backtest/{run_id}/status").json()["data"]
    assert status["status"] == "completed"
    assert status["startedAt"] and status["finishedAt"]

    detail = client.get(f"/api/backtest/{run_id}")
    assert detail.status_code == 200
    detail_body = detail.json()
    assert detail_body["ok"] is True
    manifest = detail_body["data"]["manifest"]
    metrics = detail_body["data"]["metrics"]
    assert manifest["status"] == "completed"
    assert manifest["actual_start"] >= "20260401"
    assert metrics["trade_count"] > 0
    for key in (
        "run_id",
        "created_at",
        "strategy",
        "strategy_fingerprint",
        "actual_start",
        "actual_end",
        "config",
        "warnings",
    ):
        assert key in manifest
    for key in (
        "total_return",
        "annual_return",
        "sharpe",
        "max_drawdown",
        "trade_count",
        "rejection_reasons",
        "total_fees",
    ):
        assert key in metrics

    nav = client.get(f"/api/backtest/{run_id}/nav")
    assert nav.status_code == 200
    nav_items = nav.json()["data"]["items"]
    assert len(nav_items) > 0
    for key in ("date", "nav", "portfolio_value", "cash", "drawdown"):
        assert key in nav_items[0]

    trades = client.get(f"/api/backtest/{run_id}/trades")
    assert trades.status_code == 200
    trade_items = trades.json()["data"]["items"]
    assert len(trade_items) > 0
    for key in ("date", "ts_code", "action", "status", "rejection_reason", "shares"):
        assert key in trade_items[0]

    listing = client.get("/api/backtest/runs", params={"limit": 10})
    assert listing.status_code == 200
    assert listing.json()["data"]["total"] >= 1
    assert listing.json()["data"]["items"][0]["status"] == "completed"


def test_unknown_run_returns_run_not_found(client):
    response = client.get("/api/backtest/not-a-run")
    assert response.status_code == 404
    body = response.json()
    assert body["ok"] is False
    assert body["error"] == "RUN_NOT_FOUND"


def test_unknown_strategy_returns_invalid_param(client):
    response = client.post("/api/backtest/run", json=_run_payload(strategy="nope"))
    assert response.status_code == 400
    assert response.json()["error"] == "INVALID_PARAM"


def test_failed_job_reports_failed_status(client):
    response = client.post(
        "/api/backtest/run",
        json=_run_payload(startDate="1990-01-01", endDate="1990-12-31"),
    )
    assert response.status_code == 200
    run_id = response.json()["data"]["runId"]

    assert _wait_for_completion(client, run_id) == "failed"
    status = client.get(f"/api/backtest/{run_id}/status").json()["data"]
    assert status["status"] == "failed"
    assert status["error"]


def test_reversed_dates_returns_invalid_param(client):
    response = client.post(
        "/api/backtest/run",
        json=_run_payload(startDate="2026-09-30", endDate="2026-04-01"),
    )
    assert response.status_code == 400
    assert response.json()["error"] == "INVALID_PARAM"
