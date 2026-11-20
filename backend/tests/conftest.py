from __future__ import annotations

import atexit
import os
import shutil
import sys
import tempfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "backend"))

SAMPLES_SRC = ROOT / "data" / "samples"


def _isolated_data_dir() -> Path:
    """测试只使用离线样本，隔离本机 data/raw 与 .env 的影响。"""
    if not SAMPLES_SRC.exists():
        return ROOT / "data"
    target = Path(tempfile.mkdtemp(prefix="web_quant_test_data_"))
    shutil.copytree(SAMPLES_SRC, target / "samples")
    atexit.register(shutil.rmtree, target, True)
    return target


TEST_DATA_DIR = _isolated_data_dir()

# 直接赋值而不是 setdefault：显式覆盖 .env 中的 auto 模式、真实数据目录与 token
os.environ["WEB_QUANT_MODE"] = "offline"
os.environ["WEB_QUANT_DATA_DIR"] = str(TEST_DATA_DIR)
os.environ["WEB_QUANT_DB"] = str(TEST_DATA_DIR / "meta" / "test.db")
os.environ["TS_TOKEN"] = ""

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
