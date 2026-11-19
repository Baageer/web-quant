"""数据运维 API。"""

from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field

from app.api.deps import Services, get_services
from app.models.schemas import ConfigData, DataStatus, Envelope, RefreshResult

router = APIRouter(prefix="/api", tags=["data"])


class RefreshRequest(BaseModel):
    trade_date: str | None = Field(default=None, alias="tradeDate")
    datasets: list[str] | None = None
    force: bool = False

    model_config = {"populate_by_name": True}


@router.get("/data/status", response_model=DataStatus)
def data_status(services: Services = Depends(get_services)) -> DataStatus:
    return services.market.get_status()


@router.get("/config", response_model=ConfigData)
def config(services: Services = Depends(get_services)) -> ConfigData:
    return services.market.get_config()


@router.post("/data/refresh", response_model=Envelope[RefreshResult])
def refresh_data(
    payload: RefreshRequest | None = None,
    services: Services = Depends(get_services),
):
    payload = payload or RefreshRequest()
    trade_date = payload.trade_date or services.calendar.latest_trade_date()
    results = services.refresh.refresh_date(
        trade_date,
        payload.datasets,
        force=payload.force,
    )
    data = RefreshResult(trade_date=trade_date, datasets=results)
    return Envelope(
        ok=True,
        source="live",
        as_of=datetime.now().astimezone().isoformat(timespec="seconds"),
        stale=False,
        warnings=[],
        data=data,
    )
