"""机会雷达 API。"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query

from app.api.deps import Services, get_services
from app.models.schemas import Envelope, RadarDetail, RadarOpportunityList

router = APIRouter(prefix="/api/radar", tags=["radar"])


@router.get("/opportunities", response_model=Envelope[RadarOpportunityList])
def radar_opportunities(
    date: str | None = Query(None, description="交易日 YYYYMMDD，默认最新"),
    top_k: int = Query(50, alias="topK", ge=1, le=200),
    min_amount: float | None = Query(
        None, alias="minAmount", description="20 日平均成交额下限（元）"
    ),
    exclude_st: bool = Query(True, alias="excludeST"),
    industry: str | None = Query(None, description="行业关键词"),
    services: Services = Depends(get_services),
) -> Envelope[RadarOpportunityList]:
    return services.radar.get_opportunities(
        date,
        top_k=top_k,
        min_amount=min_amount,
        exclude_st=exclude_st,
        industry=industry,
    )


@router.get("/{ts_code}", response_model=Envelope[RadarDetail])
def radar_detail(
    ts_code: str,
    date: str | None = Query(None),
    services: Services = Depends(get_services),
) -> Envelope[RadarDetail]:
    return services.radar.get_detail(ts_code, date)
