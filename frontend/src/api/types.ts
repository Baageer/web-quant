export interface Envelope<T> {
  ok: boolean;
  source: string;
  asOf: string;
  stale: boolean;
  warnings: string[];
  data: T;
}

export interface IndexQuote {
  code: string;
  name: string;
  close: number;
  pctChg: number;
  amount: number;
}

export interface Breadth {
  up: number;
  down: number;
  flat: number;
  limitUp: number;
  limitDown: number;
  total: number;
  medianPct: number;
}

export interface AmountInfo {
  total: number;
  unit: string;
  ratio5d: number;
}

export interface SectorItem {
  name: string;
  pctChg: number;
  count: number;
  amount: number;
}

export interface RankItem {
  tsCode: string;
  name: string;
  industry?: string | null;
  close: number;
  pctChg: number;
  amount: number;
  turnoverRate?: number | null;
  volumeRatio?: number | null;
}

export interface Leaders {
  gainers: RankItem[];
  losers: RankItem[];
  turnover: RankItem[];
}

export interface Regime {
  label: string;
  score: number;
  reasons: string[];
}

export interface MarketOverview {
  tradeDate: string;
  indices: IndexQuote[];
  breadth: Breadth;
  amount: AmountInfo;
  sectorsTop: SectorItem[];
  sectorsBottom: SectorItem[];
  leaders: Leaders;
  regime: Regime;
}

export interface TickerList {
  tradeDate: string;
  total: number;
  items: RankItem[];
}

export interface ConfigData {
  mode: string;
  networkEnabled: boolean;
  tokenConfigured: boolean;
  latestTradeDate: string;
  universeSize: number;
  apiVersion: string;
}

export interface DatasetStatus {
  dataset: string;
  latestDate?: string | null;
  complete: boolean;
  origin?: string | null;
  rows?: number | null;
  reason?: string | null;
}

export interface DataStatus {
  mode: string;
  expectedTradeDate: string;
  tokenConfigured: boolean;
  calendarFallback: boolean;
  datasets: DatasetStatus[];
  recentRuns: Record<string, unknown>[];
}

export interface ApiErrorBody {
  ok: false;
  error: string;
  message: string;
  warnings?: string[];
}

export interface TradePlan {
  entryLow: number;
  entryHigh: number;
  stopLoss: number;
  target1: number;
  target2: number;
  rr1: number;
  rr2: number;
  positionPct: number;
}

export interface RadarOpportunity {
  rank: number;
  tsCode: string;
  name: string;
  industry?: string | null;
  close: number;
  pctChg: number;
  turnoverRate?: number | null;
  volumeRatio?: number | null;
  amount: number;
  score: number;
  confidence: number;
  signal: string;
  signalLabel: string;
  riskLevel: string;
  bias: string;
  keyReasons: string[];
  riskFlags: string[];
  tradePlan: TradePlan;
}

export interface RadarOpportunityList {
  tradeDate: string;
  total: number;
  eligible: number;
  items: RadarOpportunity[];
}

export interface FactorScore {
  key: string;
  label: string;
  value?: number | null;
  valueText: string;
  percentile: number;
  weight: number;
  contribution: number;
}

export interface RadarDetail extends RadarOpportunity {
  eligible: boolean;
  excludeReason?: string | null;
  rawScore: number;
  riskPenalty: number;
  dataComplete: boolean;
  factors: FactorScore[];
}

export interface KlineBar {
  date: string;
  open: number;
  high: number;
  low: number;
  close: number;
  volume: number;
  amount: number;
  pctChg: number;
}

export interface MaSeries {
  ma5: (number | null)[];
  ma10: (number | null)[];
  ma20: (number | null)[];
  ma60: (number | null)[];
}

export interface BollSeries {
  upper: (number | null)[];
  middle: (number | null)[];
  lower: (number | null)[];
}

export interface MacdSeries {
  diff: (number | null)[];
  dea: (number | null)[];
  hist: (number | null)[];
}

export interface KdjSeries {
  k: (number | null)[];
  d: (number | null)[];
  j: (number | null)[];
}

export interface VolMaSeries {
  ma5: (number | null)[];
  ma20: (number | null)[];
}

export interface KlineIndicators {
  ma: MaSeries;
  boll: BollSeries;
  macd: MacdSeries;
  rsi: (number | null)[];
  atr: (number | null)[];
  kdj: KdjSeries;
  volMa: VolMaSeries;
}

export interface KlineData {
  tsCode: string;
  name: string;
  period: string;
  adjust: string;
  bars: KlineBar[];
  indicators: KlineIndicators;
}

export interface SignalTradePlan extends TradePlan {
  entryType: string;
  executable: boolean;
  executionNote: string;
  shares: number;
}

export interface ScorePart {
  factor: string;
  label: string;
  weight: number;
  raw: number;
  contribution: number;
  reason: string;
}

export interface EvidenceGroup {
  bias: string;
  score: number;
  reasons: string[];
}

export interface SignalEvidence {
  technical: EvidenceGroup;
  capital: EvidenceGroup;
  sentiment: EvidenceGroup;
}

export interface StockSignal {
  tsCode: string;
  name: string;
  tradeDate: string;
  period: string;
  close: number;
  pctChg: number;
  score: number;
  signal: string;
  signalLabel: string;
  confidence: number;
  direction: string;
  marketState: string;
  dailyScore: number;
  weeklyScore: number;
  tradePlan: SignalTradePlan;
  scoreBreakdown: ScorePart[];
  evidence: SignalEvidence;
  riskFlags: string[];
  warnings: string[];
  constraints: string[];
}

export interface SignalBatchItem {
  tsCode: string;
  name: string;
  close: number;
  pctChg: number;
  score: number;
  signal: string;
  signalLabel: string;
  confidence: number;
  direction: string;
  entryLow: number;
  stopLoss: number;
  target1: number;
  executable: boolean;
  error?: string | null;
}

export interface SignalBatch {
  tradeDate: string;
  items: SignalBatchItem[];
}
