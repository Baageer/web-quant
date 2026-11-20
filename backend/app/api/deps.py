"""服务依赖装配。"""

from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache

from app.core.config import get_settings
from app.data.calendar import TradeCalendar
from app.data.repository import Repository
from app.services.backtest_service import BacktestService
from app.services.kline_service import KlineService
from app.services.market_service import MarketService
from app.services.radar_service import RadarService
from app.services.refresh_service import RefreshService
from app.services.signal_service import SignalService


@dataclass(frozen=True)
class Services:
    repository: Repository
    calendar: TradeCalendar
    refresh: RefreshService
    market: MarketService
    radar: RadarService
    kline: KlineService
    signal: SignalService
    backtest: BacktestService


@lru_cache(maxsize=1)
def build_services() -> Services:
    settings = get_settings()
    repository = Repository(settings.data_dir, settings.db_path, settings.samples_dir)
    calendar = TradeCalendar(repository)
    refresh = RefreshService(settings, repository, calendar)
    market = MarketService(settings, repository, calendar, refresh)
    radar = RadarService(settings, repository, calendar, refresh)
    kline = KlineService(settings, repository, calendar)
    signal = SignalService(settings, repository, calendar, kline)
    backtest = BacktestService(settings, repository, calendar)
    return Services(
        repository=repository,
        calendar=calendar,
        refresh=refresh,
        market=market,
        radar=radar,
        kline=kline,
        signal=signal,
        backtest=backtest,
    )


def get_services() -> Services:
    return build_services()
