"""回测 API（docs/BACKTEST_DESIGN.md §10.2）。"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query

from app.api.deps import Services, get_services
from app.models.schemas import (
    BacktestAuditData,
    BacktestAuditRequest,
    BacktestNavData,
    BacktestRequest,
    BacktestRunDetail,
    BacktestRunList,
    BacktestRunStatus,
    BacktestRunSummary,
    BacktestTradeData,
    Envelope,
)

router = APIRouter(prefix="/api", tags=["backtest"])


@router.post("/backtest/run", response_model=Envelope[BacktestRunSummary])
def run_backtest(
    payload: BacktestRequest,
    services: Services = Depends(get_services),
) -> Envelope[BacktestRunSummary]:
    return services.backtest.submit(payload)


@router.get("/backtest/runs", response_model=Envelope[BacktestRunList])
def list_backtest_runs(
    limit: int = Query(20, ge=1, le=100),
    offset: int = Query(0, ge=0),
    services: Services = Depends(get_services),
) -> Envelope[BacktestRunList]:
    return services.backtest.list_runs(limit=limit, offset=offset)


@router.get("/backtest/{run_id}", response_model=Envelope[BacktestRunDetail])
def get_backtest_run(
    run_id: str,
    services: Services = Depends(get_services),
) -> Envelope[BacktestRunDetail]:
    return services.backtest.get_run(run_id)


@router.post("/backtest/{run_id}/audit", response_model=Envelope[BacktestAuditData])
def run_backtest_audit(
    run_id: str,
    payload: BacktestAuditRequest | None = None,
    services: Services = Depends(get_services),
) -> Envelope[BacktestAuditData]:
    body = payload or BacktestAuditRequest()
    return services.backtest.audit_run(
        run_id,
        param_grid=body.param_grid,
        thresholds=body.thresholds,
        n_blocks=body.n_blocks,
        n_test_blocks=body.n_test_blocks,
        purge_blocks=body.purge_blocks,
    )


@router.get("/backtest/{run_id}/audit", response_model=Envelope[BacktestAuditData])
def get_backtest_audit(
    run_id: str,
    services: Services = Depends(get_services),
) -> Envelope[BacktestAuditData]:
    return services.backtest.get_audit(run_id)


@router.get("/backtest/{run_id}/status", response_model=Envelope[BacktestRunStatus])
def get_backtest_status(
    run_id: str,
    services: Services = Depends(get_services),
) -> Envelope[BacktestRunStatus]:
    return services.backtest.get_status(run_id)


@router.get("/backtest/{run_id}/nav", response_model=Envelope[BacktestNavData])
def get_backtest_nav(
    run_id: str,
    services: Services = Depends(get_services),
) -> Envelope[BacktestNavData]:
    return services.backtest.get_nav(run_id)


@router.get("/backtest/{run_id}/trades", response_model=Envelope[BacktestTradeData])
def get_backtest_trades(
    run_id: str,
    services: Services = Depends(get_services),
) -> Envelope[BacktestTradeData]:
    return services.backtest.get_trades(run_id)
