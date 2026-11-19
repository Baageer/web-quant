from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "backend"))

os.environ.setdefault("WEB_QUANT_MODE", "offline")
os.environ.setdefault("WEB_QUANT_DATA_DIR", str(ROOT / "data"))

from app.core.config import build_settings  # noqa: E402
from app.data.calendar import TradeCalendar  # noqa: E402
from app.data.repository import Repository  # noqa: E402
from app.services.kline_service import KlineService  # noqa: E402
from app.services.market_service import MarketService  # noqa: E402
from app.services.radar_service import RadarService  # noqa: E402
from app.services.signal_service import SignalService  # noqa: E402


@pytest.fixture(scope="session")
def settings():
    return build_settings()


@pytest.fixture()
def repository(settings, tmp_path):
    return Repository(settings.data_dir, tmp_path / "test.db", settings.samples_dir)


@pytest.fixture()
def calendar(repository):
    return TradeCalendar(repository)


@pytest.fixture()
def market_service(settings, repository, calendar):
    return MarketService(settings, repository, calendar)


@pytest.fixture()
def radar_service(settings, repository, calendar):
    return RadarService(settings, repository, calendar)


@pytest.fixture()
def kline_service(settings, repository, calendar):
    return KlineService(settings, repository, calendar)


@pytest.fixture()
def signal_service(settings, repository, calendar, kline_service):
    return SignalService(settings, repository, calendar, kline_service)
