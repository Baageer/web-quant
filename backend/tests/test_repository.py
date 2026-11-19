from __future__ import annotations

import pandas as pd
import pytest

from app.data.quality import validate_dataset
from app.data.repository import Repository


def _daily_frame(trade_date: str, rows: int = 5) -> pd.DataFrame:
    return pd.DataFrame(
        {
            "ts_code": [f"6000{index:02d}.SH" for index in range(rows)],
            "trade_date": [trade_date] * rows,
            "open": [10.0] * rows,
            "high": [10.5] * rows,
            "low": [9.8] * rows,
            "close": [10.2] * rows,
            "pre_close": [10.0] * rows,
            "pct_chg": [2.0] * rows,
            "vol": [10000.0] * rows,
            "amount": [100000.0] * rows,
        }
    )


def test_repository_sample_roundtrip(tmp_path):
    repository = Repository(tmp_path / "data", tmp_path / "data" / "meta" / "test.db")
    frame = _daily_frame("20260105")

    complete, reason, path = repository.write_dataset(
        "daily", "20260105", frame, layer="sample", origin="sample"
    )
    assert complete is True
    assert reason is None
    assert path.exists()

    loaded = repository.read_dataset("daily", "20260105")
    assert loaded is not None
    assert loaded.origin == "sample"
    assert len(loaded.frame) == 5
    assert repository.available_dates("daily") == ["20260105"]


def test_repository_rejects_small_raw_dataset(tmp_path):
    repository = Repository(tmp_path / "data", tmp_path / "data" / "meta" / "test.db")
    frame = _daily_frame("20260105")

    complete, reason, path = repository.write_dataset("daily", "20260105", frame)
    assert complete is False
    assert reason and reason.startswith("too_few_rows")
    assert not path.exists()


def test_quality_checks_required_columns():
    frame = _daily_frame("20260105").drop(columns=["close"])
    complete, reason = validate_dataset("daily", frame, "20260105", allow_small=True)
    assert complete is False
    assert reason is not None and "missing_columns" in reason


def test_trade_calendar_roundtrip(tmp_path):
    repository = Repository(tmp_path / "data", tmp_path / "data" / "meta" / "test.db")
    frame = pd.DataFrame({"cal_date": ["20260105", "20260106"], "is_open": [1, 1]})
    repository.write_trade_cal(frame, layer="sample", origin="sample")

    loaded = repository.read_trade_cal()
    assert loaded is not None
    assert len(loaded.frame) == 2
