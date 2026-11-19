from __future__ import annotations

import pytest

from app.core.errors import InvalidParamError, SymbolNotFoundError


def test_signal_structure(signal_service):
    envelope = signal_service.get_signal("600001.SH")
    assert envelope.ok is True
    assert envelope.source == "sample"

    data = envelope.data
    assert data.ts_code == "600001.SH"
    assert data.trade_date == "20261118"
    assert -100.0 <= data.score <= 100.0
    assert data.signal in {"STRONG_BUY", "BUY", "WATCH", "HOLD", "REDUCE", "SELL"}
    assert data.signal_label
    assert 25.0 <= data.confidence <= 95.0
    assert data.direction in {"bullish", "bearish", "neutral"}
    assert data.market_state in {"trending", "ranging", "volatile"}
    assert len(data.score_breakdown) == 6
    assert sum(part.weight for part in data.score_breakdown) == pytest.approx(1.0)
    assert sum(part.contribution for part in data.score_breakdown) == pytest.approx(
        data.daily_score, abs=0.2
    )


def test_signal_trade_plan_and_constraints(signal_service):
    data = signal_service.get_signal("600001.SH").data
    plan = data.trade_plan

    entry = (plan.entry_low + plan.entry_high) / 2
    assert plan.stop_loss < plan.entry_low <= plan.entry_high
    assert plan.target1 > entry
    assert plan.target1 < plan.target2
    assert plan.rr1 >= 0 and plan.rr2 >= 0
    assert plan.rr2 >= plan.rr1
    assert 0 <= plan.position_pct <= 20
    assert plan.shares >= 0 and plan.shares % 100 == 0
    assert plan.entry_type in {"breakout", "pullback"}
    assert isinstance(plan.executable, bool)
    assert plan.execution_note
    assert "T+1" in "".join(data.constraints)
    assert "100 股整手" in "".join(data.constraints)


def test_signal_evidence(signal_service):
    data = signal_service.get_signal("600002.SH").data
    for group in (data.evidence.technical, data.evidence.capital, data.evidence.sentiment):
        assert group.bias in {"bullish", "bearish", "neutral"}
        assert 0.0 <= group.score <= 100.0
        assert group.reasons


def test_signal_batch_and_errors(signal_service):
    batch = signal_service.get_batch("600001.SH,600002.SH,999999.SH").data
    assert len(batch.items) == 3
    assert batch.items[0].ts_code == "600001.SH"
    assert batch.items[0].entry_low > 0
    failed = [item for item in batch.items if item.error]
    assert len(failed) == 1 and failed[0].ts_code == "999999.SH"


def test_signal_batch_defaults_to_top_amount(signal_service):
    batch = signal_service.get_batch(None, limit=5).data
    assert len(batch.items) == 5


def test_signal_invalid_inputs(signal_service):
    with pytest.raises(InvalidParamError):
        signal_service.get_signal("600001.SH", period="weekly")
    with pytest.raises(SymbolNotFoundError):
        signal_service.get_signal("999999.SH")
