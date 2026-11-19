from __future__ import annotations

import pytest

from app.core.errors import SymbolNotFoundError
from app.services.scoring import (
    confidence_for,
    signal_for,
)


def test_opportunities_sorted_and_explainable(radar_service):
    envelope = radar_service.get_opportunities(top_k=50)
    assert envelope.ok is True
    assert envelope.source == "sample"

    data = envelope.data
    assert data.trade_date == "20261118"
    assert 1 <= len(data.items) <= 50
    assert data.total >= len(data.items)

    scores = [item.score for item in data.items]
    assert scores == sorted(scores, reverse=True)

    for item in data.items:
        assert 0 <= item.score <= 100
        assert 25 <= item.confidence <= 95
        assert item.signal in {"STRONG_BUY", "BUY", "WATCH", "NEUTRAL", "AVOID"}
        assert item.risk_level in {"low", "medium", "high"}
        assert item.key_reasons
        assert item.trade_plan.stop_loss < item.close < item.trade_plan.target1
        assert item.trade_plan.rr1 > 0


def test_topk_and_industry_filter(radar_service):
    small = radar_service.get_opportunities(top_k=5).data
    assert len(small.items) == 5

    bank = radar_service.get_opportunities(top_k=50, industry="银行").data
    assert bank.total > 0
    assert all("银行" in (item.industry or "") for item in bank.items)


def test_detail_factor_breakdown_matches_score(radar_service):
    top = radar_service.get_opportunities(top_k=5).data.items[0]
    detail = radar_service.get_detail(top.ts_code).data

    assert detail.eligible is True
    assert detail.ts_code == top.ts_code
    assert len(detail.factors) == 7
    assert sum(item.weight for item in detail.factors) == pytest.approx(1.0)

    contribution = sum(item.contribution for item in detail.factors)
    assert contribution == pytest.approx(detail.raw_score, abs=0.2)
    expected = max(0.0, min(100.0, detail.raw_score - detail.risk_penalty))
    assert detail.score == pytest.approx(expected, abs=0.05)


def test_detail_unknown_symbol(radar_service):
    with pytest.raises(SymbolNotFoundError):
        radar_service.get_detail("999999.SH")


def test_signal_and_confidence_mapping():
    assert signal_for(85) == ("STRONG_BUY", "多维共振")
    assert signal_for(70)[0] == "BUY"
    assert signal_for(55)[0] == "WATCH"
    assert signal_for(40)[0] == "NEUTRAL"
    assert signal_for(20)[0] == "AVOID"

    assert confidence_for(50, False) == pytest.approx(40.0)
    assert confidence_for(50, True) == pytest.approx(50.0)
    assert confidence_for(0, True) == pytest.approx(70.0)
    assert confidence_for(100, False) == pytest.approx(60.0)


def test_risk_penalty_applied(radar_service):
    envelope = radar_service.get_opportunities(top_k=100)
    penalized = [item for item in envelope.data.items if item.risk_flags]
    assert penalized, "样本中应存在带风险标记的标的"
    assert any(item.risk_level in {"medium", "high"} for item in envelope.data.items)
