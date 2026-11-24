import type {
  ApiErrorBody,
  BacktestAuditData,
  BacktestAuditRequest,
  BacktestNav,
  BacktestRequest,
  BacktestRunDetail,
  BacktestRunList,
  BacktestRunStatus,
  BacktestRunSummary,
  BacktestTrades,
  ConfigData,
  DataStatus,
  Envelope,
  KlineData,
  MarketOverview,
  RadarDetail,
  RadarOpportunityList,
  SignalBatch,
  StockSignal,
  TickerList,
} from "./types";

const BASE = import.meta.env.VITE_API_BASE ?? "";

export class ApiError extends Error {
  readonly code: string;
  readonly status: number;

  constructor(message: string, code: string, status: number) {
    super(message);
    this.name = "ApiError";
    this.code = code;
    this.status = status;
  }
}

interface RequestOptions {
  method?: "GET" | "POST";
  body?: unknown;
}

async function request<T>(path: string, options: RequestOptions = {}): Promise<T> {
  const init: RequestInit = { method: options.method ?? "GET" };
  if (options.body !== undefined) {
    init.headers = { "Content-Type": "application/json" };
    init.body = JSON.stringify(options.body);
  }
  const response = await fetch(`${BASE}${path}`, init);
  let body: unknown;
  try {
    body = await response.json();
  } catch {
    throw new ApiError("服务返回了非 JSON 响应", "BAD_RESPONSE", response.status);
  }
  if (!response.ok) {
    const error = body as ApiErrorBody;
    throw new ApiError(
      error.message || "请求失败",
      error.error || "HTTP_ERROR",
      response.status,
    );
  }
  return body as T;
}

export interface TickerQuery {
  date?: string;
  sort?: string;
  order?: "asc" | "desc";
  limit?: number;
  offset?: number;
  search?: string;
}

export interface RadarQuery {
  date?: string;
  topK?: number;
  minAmount?: number;
  excludeST?: boolean;
  industry?: string;
}

export interface KlineQuery {
  period?: "daily" | "weekly" | "monthly";
  adjust?: "qfq" | "hfq" | "raw";
  limit?: number;
  date?: string;
}

export const api = {
  overview(date?: string): Promise<Envelope<MarketOverview>> {
    const query = date ? `?date=${encodeURIComponent(date)}` : "";
    return request(`/api/market/overview${query}`);
  },
  tickers(params: TickerQuery = {}): Promise<Envelope<TickerList>> {
    const search = new URLSearchParams();
    if (params.date) search.set("date", params.date);
    if (params.sort) search.set("sort", params.sort);
    if (params.order) search.set("order", params.order);
    if (params.limit != null) search.set("limit", String(params.limit));
    if (params.offset != null) search.set("offset", String(params.offset));
    if (params.search) search.set("search", params.search);
    return request(`/api/market/tickers?${search.toString()}`);
  },
  config(): Promise<ConfigData> {
    return request("/api/config");
  },
  status(): Promise<DataStatus> {
    return request("/api/data/status");
  },
  radar(params: RadarQuery = {}): Promise<Envelope<RadarOpportunityList>> {
    const search = new URLSearchParams();
    if (params.date) search.set("date", params.date);
    if (params.topK != null) search.set("topK", String(params.topK));
    if (params.minAmount != null) search.set("minAmount", String(params.minAmount));
    if (params.excludeST != null) search.set("excludeST", String(params.excludeST));
    if (params.industry) search.set("industry", params.industry);
    return request(`/api/radar/opportunities?${search.toString()}`);
  },
  radarDetail(tsCode: string, date?: string): Promise<Envelope<RadarDetail>> {
    const query = date ? `?date=${encodeURIComponent(date)}` : "";
    return request(`/api/radar/${encodeURIComponent(tsCode)}${query}`);
  },
  kline(tsCode: string, params: KlineQuery = {}): Promise<Envelope<KlineData>> {
    const search = new URLSearchParams();
    if (params.period) search.set("period", params.period);
    if (params.adjust) search.set("adjust", params.adjust);
    if (params.limit != null) search.set("limit", String(params.limit));
    if (params.date) search.set("date", params.date);
    return request(`/api/kline/${encodeURIComponent(tsCode)}?${search.toString()}`);
  },
  signal(tsCode: string, date?: string): Promise<Envelope<StockSignal>> {
    const query = date ? `?date=${encodeURIComponent(date)}` : "";
    return request(`/api/signal/${encodeURIComponent(tsCode)}${query}`);
  },
  signalBatch(params: {
    codes?: string;
    date?: string;
    limit?: number;
  } = {}): Promise<Envelope<SignalBatch>> {
    const search = new URLSearchParams();
    if (params.codes) search.set("codes", params.codes);
    if (params.date) search.set("date", params.date);
    if (params.limit != null) search.set("limit", String(params.limit));
    return request(`/api/signal/batch?${search.toString()}`);
  },
  runBacktest(payload: BacktestRequest): Promise<Envelope<BacktestRunSummary>> {
    return request("/api/backtest/run", { method: "POST", body: payload });
  },
  backtestRuns(limit = 20, offset = 0): Promise<Envelope<BacktestRunList>> {
    const search = new URLSearchParams();
    search.set("limit", String(limit));
    search.set("offset", String(offset));
    return request(`/api/backtest/runs?${search.toString()}`);
  },
  backtestStatus(runId: string): Promise<Envelope<BacktestRunStatus>> {
    return request(`/api/backtest/${encodeURIComponent(runId)}/status`);
  },
  backtestDetail(runId: string): Promise<Envelope<BacktestRunDetail>> {
    return request(`/api/backtest/${encodeURIComponent(runId)}`);
  },
  backtestNav(runId: string): Promise<Envelope<BacktestNav>> {
    return request(`/api/backtest/${encodeURIComponent(runId)}/nav`);
  },
  backtestTrades(runId: string): Promise<Envelope<BacktestTrades>> {
    return request(`/api/backtest/${encodeURIComponent(runId)}/trades`);
  },
  backtestAudit(runId: string): Promise<Envelope<BacktestAuditData>> {
    return request(`/api/backtest/${encodeURIComponent(runId)}/audit`);
  },
  runBacktestAudit(
    runId: string,
    payload: BacktestAuditRequest = {},
  ): Promise<Envelope<BacktestAuditData>> {
    return request(`/api/backtest/${encodeURIComponent(runId)}/audit`, {
      method: "POST",
      body: payload,
    });
  },
};
