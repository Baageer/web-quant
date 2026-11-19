"""行情相关 API。"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query

from app.api.deps import Services, get_services
from app.models.schemas import (
    Breadth,
    Envelope,
    MarketOverview,
    SectorList,
    TickerList,
)

router = APIRouter(prefix="/api/market", tags=["market"])


@router.get("/overview", response_model=Envelope[MarketOverview])
def market_overview(
    date: str | None = Query(None, description="交易日 YYYYMMDD，默认最新"),
    services: Services = Depends(get_services),
) -> Envelope[MarketOverview]:
    return services.market.get_overview(date)


@router.get("/tickers", response_model=Envelope[TickerList])
def market_tickers(
    date: str | None = Query(None),
    sort: str = Query("pctChg"),
    order: str = Query("desc", pattern="^(asc|desc)$"),
    limit: int = Query(100, ge=1, le=500),
    offset: int = Query(0, ge=0),
    search: str | None = Query(None),
    services: Services = Depends(get_services),
) -> Envelope[TickerList]:
    return services.market.get_tickers(
        date,
        sort=sort,
        order=order,
        limit=limit,
        offset=offset,
        search=search,
    )


@router.get("/breadth", response_model=Envelope[Breadth])
def market_breadth(
    date: str | None = Query(None),
    services: Services = Depends(get_services),
) -> Envelope[Breadth]:
    return services.market.get_breadth(date)


@router.get("/sectors", response_model=Envelope[SectorList])
def market_sectors(
    date: str | None = Query(None),
    limit: int = Query(10, ge=1, le=30),
    services: Services = Depends(get_services),
) -> Envelope[SectorList]:
    return services.market.get_sectors(date, limit=limit)
