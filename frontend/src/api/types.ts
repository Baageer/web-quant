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

// ---- 回测（P4.4） ----

export interface BacktestRequest {
  startDate: string;
  endDate: string;
  strategy: string;
  params: Record<string, unknown>;
  initialCash?: number;
  symbols?: string[];
  maxPositions?: number;
  maxPosition?: number;
  maxIndustry?: number;
  maxTurnover?: number | null;
  rebalance?: string;
  stopLoss?: number;
  takeProfit?: number;
  atrWindow?: number;
  atrTakeProfit?: number;
  stopCooldown?: string;
}

export interface BacktestRunSummary {
  runId: string;
  status: string;
  strategy: string;
  startDate: string;
  endDate: string;
  actualStart?: string | null;
  actualEnd?: string | null;
  initialCash: number;
  finalValue: number;
  totalReturn: number;
  tradeCount: number;
  warnings: string[];
}

export interface BacktestRunListItem {
  runId: string;
  strategy: string;
  startDate: string;
  endDate: string;
  createdAt?: string | null;
  status: string;
  totalReturn: number | null;
  tradeCount: number | null;
}

export interface BacktestRunList {
  total: number;
  items: BacktestRunListItem[];
}

export interface BacktestRunStatus {
  runId: string;
  status: string;
  error?: string | null;
  createdAt?: string | null;
  startedAt?: string | null;
  finishedAt?: string | null;
}

export interface BacktestManifest {
  run_id?: string;
  status?: string;
  strategy?: string;
  start_date?: string;
  end_date?: string;
  actual_start?: string | null;
  actual_end?: string | null;
  params?: Record<string, unknown>;
  initial_cash?: number;
  engine_version?: string;
  verdict?: string;
  audited_at?: string;
  warnings?: string[];
  error?: string | null;
}

export interface BacktestMetrics {
  total_return: number;
  annual_return: number;
  annual_volatility: number;
  sharpe: number;
  sortino: number;
  max_drawdown: number;
  max_drawdown_days: number;
  calmar: number;
  final_value: number;
  years: number;
  trade_count: number;
  filled_count: number;
  rejected_count: number;
  expired_count: number;
  win_rate: number;
  profit_factor: number;
  profit_loss_ratio: number;
  avg_holding_days: number;
  total_fees: number;
  fee_ratio: number;
  turnover_rate: number;
  rejection_reasons: Record<string, number>;
  suspension_deferrals: number;
  benchmark_return: number;
  excess_return: number;
  tracking_error: number;
  information_ratio: number;
  alpha: number;
  beta: number;
  monthly_returns: Record<string, number>;
  yearly_returns: Record<string, number>;
}

export interface BacktestRunDetail {
  runId: string;
  manifest: BacktestManifest;
  metrics: BacktestMetrics;
}

export interface BacktestNavPoint {
  date: string;
  portfolio_value: number;
  nav: number;
  cash: number;
  cash_ratio: number;
  daily_return: number;
  drawdown: number;
}

export interface BacktestNav {
  runId: string;
  items: BacktestNavPoint[];
}

export interface BacktestTrade {
  date: string;
  signal_date: string;
  ts_code: string;
  action: string;
  status: string;
  rejection_reason: string;
  shares: number;
  price: number;
  amount: number;
  commission: number;
  stamp_duty: number;
  transfer_fee: number;
  slippage_cost: number;
  profit: number;
  reason: string;
}

export interface BacktestTrades {
  runId: string;
  items: BacktestTrade[];
}

export interface BacktestAuditTrial {
  params: Record<string, unknown>;
  sharpe: number;
  total_return: number;
  max_drawdown: number;
  trade_count: number;
}

export interface BacktestAudit {
  created_at: string;
  strategy: string;
  start_date: string;
  end_date: string;
  params: Record<string, unknown>;
  thresholds: Record<string, number>;
  trials: {
    count: number;
    configs: string[];
    details: BacktestAuditTrial[];
  };
  observed: {
    label: string;
    sharpe: number;
    trading_days: number;
  };
  pbo: {
    pbo: number;
    combinations: number;
    configs: number;
    blocks: number;
  };
  cpcv: {
    splits: number;
    mean_test_sharpe: number;
    std_test_sharpe: number;
    positive_ratio: number;
    paths: { split: number; selected: string; train_sharpe: number; test_sharpe: number }[];
    selected: Record<string, number>;
  };
  dsr: {
    dsr: number;
    z: number;
    sr0: number;
    haircut_sharpe: number;
    observed_sharpe: number;
    trials: number;
  };
  verdict: "accepted" | "rejected";
  reasons: string[];
  notes: string[];
}

export interface BacktestAuditData {
  runId: string;
  audit: BacktestAudit;
}

export interface BacktestAuditRequest {
  paramGrid?: Record<string, unknown[]>;
  thresholds?: Record<string, number>;
  nBlocks?: number;
  nTestBlocks?: number;
  purgeBlocks?: number;
}
