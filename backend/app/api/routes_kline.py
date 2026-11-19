"""K 线与交易信号 API（DESIGN.md 9.4）。"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query

from app.api.deps import Services, get_services
from app.models.schemas import Envelope, KlineData, SignalBatch, StockSignal

router = APIRouter(prefix="/api", tags=["kline"])


@router.get("/kline/{ts_code}", response_model=Envelope[KlineData])
def kline(
    ts_code: str,
    period: str = Query("daily", pattern="^(daily|weekly|monthly)$"),
    adjust: str = Query("qfq", pattern="^(qfq|hfq|raw)$"),
    limit: int = Query(250, ge=20, le=1000),
    date: str | None = Query(None, description="交易日 YYYYMMDD，默认最新"),
    services: Services = Depends(get_services),
) -> Envelope[KlineData]:
    return services.kline.get_kline(
        ts_code,
        period=period,
        adjust=adjust,
        limit=limit,
        date=date,
    )


@router.get("/signal/batch", response_model=Envelope[SignalBatch])
def signal_batch(
    codes: str | None = Query(
        None, description="逗号分隔的代码；为空时取最新交易日成交额前 N"
    ),
    date: str | None = Query(None),
    limit: int = Query(20, ge=1, le=50),
    services: Services = Depends(get_services),
) -> Envelope[SignalBatch]:
    return services.signal.get_batch(codes, date=date, limit=limit)


@router.get("/signal/{ts_code}", response_model=Envelope[StockSignal])
def signal(
    ts_code: str,
    period: str = Query("daily", pattern="^(daily)$"),
    date: str | None = Query(None),
    services: Services = Depends(get_services),
) -> Envelope[StockSignal]:
    return services.signal.get_signal(ts_code, date=date, period=period)
