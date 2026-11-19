"""API 响应模型。"""

from __future__ import annotations

from typing import Generic, TypeVar

from pydantic import BaseModel, ConfigDict, Field


def to_camel(value: str) -> str:
    head, *tail = value.split("_")
    return head + "".join(word.capitalize() for word in tail)


class CamelModel(BaseModel):
    model_config = ConfigDict(alias_generator=to_camel, populate_by_name=True)


T = TypeVar("T")


class Envelope(CamelModel, Generic[T]):
    ok: bool = True
    source: str = "snapshot"
    as_of: str
    stale: bool = False
    warnings: list[str] = Field(default_factory=list)
    data: T


class IndexQuote(CamelModel):
    code: str
    name: str
    close: float
    pct_chg: float
    amount: float


class Breadth(CamelModel):
    up: int
    down: int
    flat: int
    limit_up: int
    limit_down: int
    total: int
    median_pct: float


class AmountInfo(CamelModel):
    total: float
    unit: str = "亿元"
    ratio_5d: float


class SectorItem(CamelModel):
    name: str
    pct_chg: float
    count: int
    amount: float


class RankItem(CamelModel):
    ts_code: str
    name: str
    industry: str | None = None
    close: float
    pct_chg: float
    amount: float
    turnover_rate: float | None = None
    volume_ratio: float | None = None


class Leaders(CamelModel):
    gainers: list[RankItem] = Field(default_factory=list)
    losers: list[RankItem] = Field(default_factory=list)
    turnover: list[RankItem] = Field(default_factory=list)


class Regime(CamelModel):
    label: str
    score: float
    reasons: list[str] = Field(default_factory=list)


class MarketOverview(CamelModel):
    trade_date: str
    indices: list[IndexQuote] = Field(default_factory=list)
    breadth: Breadth
    amount: AmountInfo
    sectors_top: list[SectorItem] = Field(default_factory=list)
    sectors_bottom: list[SectorItem] = Field(default_factory=list)
    leaders: Leaders
    regime: Regime


class TickerList(CamelModel):
    trade_date: str
    total: int
    items: list[RankItem] = Field(default_factory=list)


class SectorList(CamelModel):
    trade_date: str
    top: list[SectorItem] = Field(default_factory=list)
    bottom: list[SectorItem] = Field(default_factory=list)


class TradePlan(CamelModel):
    entry_low: float
    entry_high: float
    stop_loss: float
    target1: float
    target2: float
    rr1: float
    rr2: float
    position_pct: float


class FactorScore(CamelModel):
    key: str
    label: str
    value: float | None = None
    value_text: str
    percentile: float
    weight: float
    contribution: float


class RadarOpportunity(CamelModel):
    rank: int
    ts_code: str
    name: str
    industry: str | None = None
    close: float
    pct_chg: float
    turnover_rate: float | None = None
    volume_ratio: float | None = None
    amount: float
    score: float
    confidence: float
    signal: str
    signal_label: str
    risk_level: str
    bias: str
    key_reasons: list[str] = Field(default_factory=list)
    risk_flags: list[str] = Field(default_factory=list)
    trade_plan: TradePlan


class RadarOpportunityList(CamelModel):
    trade_date: str
    total: int
    eligible: int
    items: list[RadarOpportunity] = Field(default_factory=list)


class RadarDetail(RadarOpportunity):
    eligible: bool = True
    exclude_reason: str | None = None
    raw_score: float
    risk_penalty: float
    data_complete: bool
    factors: list[FactorScore] = Field(default_factory=list)


class KlineBar(CamelModel):
    date: str
    open: float
    high: float
    low: float
    close: float
    volume: float
    amount: float
    pct_chg: float


class MaSeries(CamelModel):
    ma5: list[float | None] = Field(default_factory=list)
    ma10: list[float | None] = Field(default_factory=list)
    ma20: list[float | None] = Field(default_factory=list)
    ma60: list[float | None] = Field(default_factory=list)


class BollSeries(CamelModel):
    upper: list[float | None] = Field(default_factory=list)
    middle: list[float | None] = Field(default_factory=list)
    lower: list[float | None] = Field(default_factory=list)


class MacdSeries(CamelModel):
    diff: list[float | None] = Field(default_factory=list)
    dea: list[float | None] = Field(default_factory=list)
    hist: list[float | None] = Field(default_factory=list)


class KdjSeries(CamelModel):
    k: list[float | None] = Field(default_factory=list)
    d: list[float | None] = Field(default_factory=list)
    j: list[float | None] = Field(default_factory=list)


class VolMaSeries(CamelModel):
    ma5: list[float | None] = Field(default_factory=list)
    ma20: list[float | None] = Field(default_factory=list)


class KlineIndicators(CamelModel):
    ma: MaSeries = Field(default_factory=MaSeries)
    boll: BollSeries = Field(default_factory=BollSeries)
    macd: MacdSeries = Field(default_factory=MacdSeries)
    rsi: list[float | None] = Field(default_factory=list)
    atr: list[float | None] = Field(default_factory=list)
    kdj: KdjSeries = Field(default_factory=KdjSeries)
    vol_ma: VolMaSeries = Field(default_factory=VolMaSeries)


class KlineData(CamelModel):
    ts_code: str
    name: str
    period: str
    adjust: str
    bars: list[KlineBar] = Field(default_factory=list)
    indicators: KlineIndicators = Field(default_factory=KlineIndicators)


class SignalTradePlan(TradePlan):
    entry_type: str = "pullback"
    executable: bool = True
    execution_note: str = ""
    shares: int = 0


class ScorePart(CamelModel):
    factor: str
    label: str
    weight: float
    raw: float
    contribution: float
    reason: str = ""


class EvidenceGroup(CamelModel):
    bias: str
    score: float
    reasons: list[str] = Field(default_factory=list)


class SignalEvidence(CamelModel):
    technical: EvidenceGroup
    capital: EvidenceGroup
    sentiment: EvidenceGroup


class StockSignal(CamelModel):
    ts_code: str
    name: str
    trade_date: str
    period: str = "daily"
    close: float
    pct_chg: float
    score: float
    signal: str
    signal_label: str
    confidence: float
    direction: str
    market_state: str
    daily_score: float
    weekly_score: float
    trade_plan: SignalTradePlan
    score_breakdown: list[ScorePart] = Field(default_factory=list)
    evidence: SignalEvidence
    risk_flags: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    constraints: list[str] = Field(default_factory=list)


class SignalBatchItem(CamelModel):
    ts_code: str
    name: str
    close: float
    pct_chg: float
    score: float
    signal: str
    signal_label: str
    confidence: float
    direction: str
    entry_low: float
    stop_loss: float
    target1: float
    executable: bool
    error: str | None = None


class SignalBatch(CamelModel):
    trade_date: str
    items: list[SignalBatchItem] = Field(default_factory=list)


class DatasetStatus(CamelModel):
    dataset: str
    latest_date: str | None = None
    complete: bool = False
    origin: str | None = None
    rows: int | None = None
    reason: str | None = None


class DataStatus(CamelModel):
    mode: str
    expected_trade_date: str
    token_configured: bool
    calendar_fallback: bool
    datasets: list[DatasetStatus] = Field(default_factory=list)
    recent_runs: list[dict] = Field(default_factory=list)


class ConfigData(CamelModel):
    mode: str
    network_enabled: bool
    token_configured: bool
    latest_trade_date: str
    universe_size: int
    api_version: str = "0.1.0-p2"


class RefreshResult(CamelModel):
    trade_date: str
    datasets: list[dict] = Field(default_factory=list)
