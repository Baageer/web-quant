import { useQuery } from "@tanstack/react-query";
import * as echarts from "echarts";
import type { EChartsOption } from "echarts";
import {
  Alert,
  Button,
  Card,
  Descriptions,
  Empty,
  Input,
  Progress,
  Select,
  Space,
  Spin,
  Tag,
  message,
} from "antd";
import { useEffect, useMemo, useRef, useState } from "react";

import { api } from "../../api/client";
import type {
  EvidenceGroup,
  KlineData,
  SignalTradePlan,
  StockSignal,
} from "../../api/types";

const PERIOD_OPTIONS = [
  { value: "daily", label: "日线" },
  { value: "weekly", label: "周线" },
  { value: "monthly", label: "月线" },
];

const ADJUST_OPTIONS = [
  { value: "qfq", label: "前复权" },
  { value: "hfq", label: "后复权" },
  { value: "raw", label: "不复权" },
];

const SIGNAL_COLORS: Record<string, string> = {
  STRONG_BUY: "red",
  BUY: "volcano",
  WATCH: "gold",
  HOLD: "default",
  REDUCE: "green",
  SELL: "green",
};

const DIRECTION_LABELS: Record<string, string> = {
  bullish: "偏多",
  bearish: "偏空",
  neutral: "中性",
};

const STATE_LABELS: Record<string, string> = {
  trending: "趋势",
  ranging: "震荡",
  volatile: "高波动",
};

const BIAS_LABELS: Record<string, string> = {
  bullish: "偏多",
  bearish: "偏空",
  neutral: "中性",
};

const UP_COLOR = "#d4380d";
const DOWN_COLOR = "#0f8a5f";

function biasColor(bias: string): string {
  if (bias === "bullish") return UP_COLOR;
  if (bias === "bearish") return DOWN_COLOR;
  return "#9ca3af";
}

function buildOption(
  data: KlineData,
  plan?: SignalTradePlan,
): EChartsOption {
  const dates = data.bars.map((bar) => bar.date);
  const candles = data.bars.map((bar) => [bar.open, bar.close, bar.low, bar.high]);
  const volume = data.bars.map((bar) => ({
    value: bar.volume,
    itemStyle: { color: bar.close >= bar.open ? UP_COLOR : DOWN_COLOR },
  }));
  const macdHist = data.indicators.macd.hist.map((value, index) => ({
    value,
    itemStyle: {
      color:
        (value ?? 0) >= 0 && data.bars[index].close >= data.bars[index].open
          ? UP_COLOR
          : DOWN_COLOR,
    },
  }));

  const minPrice = Math.min(...data.bars.map((bar) => bar.low));
  const maxPrice = Math.max(...data.bars.map((bar) => bar.high));
  const inRange = (value: number) =>
    value > minPrice * 0.9 && value < maxPrice * 1.1;

  const markLines = plan
    ? [
        { name: "入场下沿", value: plan.entryLow, color: "#1677ff" },
        { name: "入场上沿", value: plan.entryHigh, color: "#1677ff" },
        { name: "止损", value: plan.stopLoss, color: DOWN_COLOR },
        { name: "目标 1", value: plan.target1, color: UP_COLOR },
        { name: "目标 2", value: plan.target2, color: UP_COLOR },
      ]
        .filter((item) => inRange(item.value))
        .map((item) => ({
          name: item.name,
          yAxis: item.value,
          lineStyle: { color: item.color, type: "dashed" as const, width: 1 },
          label: {
            formatter: `${item.name} ${item.value.toFixed(2)}`,
            fontSize: 11,
            color: item.color,
          },
        }))
    : [];

  const showBars = 80;
  const start = Math.max(0, 100 - (showBars / Math.max(dates.length, 1)) * 100);

  return {
    animation: false,
    tooltip: {
      trigger: "axis",
      axisPointer: { type: "cross" },
      confine: true,
    },
    legend: {
      top: 0,
      right: 8,
      itemWidth: 12,
      itemHeight: 8,
      textStyle: { fontSize: 11 },
      data: ["MA5", "MA10", "MA20", "MA60", "BOLL上轨", "BOLL下轨"],
    },
    axisPointer: { link: [{ xAxisIndex: "all" }] },
    grid: [
      { left: 60, right: 24, top: 28, height: "32%" },
      { left: 60, right: 24, top: "41%", height: "11%" },
      { left: 60, right: 24, top: "56%", height: "12%" },
      { left: 60, right: 24, top: "72%", height: "12%" },
    ],
    xAxis: [0, 1, 2, 3].map((index) => ({
      type: "category" as const,
      gridIndex: index,
      data: dates,
      boundaryGap: true,
      axisLabel: { show: index === 3, fontSize: 11 },
      axisLine: { lineStyle: { color: "#d1d5db" } },
      axisTick: { show: false },
    })),
    yAxis: [
      {
        scale: true,
        gridIndex: 0,
        splitLine: { lineStyle: { color: "#eef1f5" } },
        axisLabel: { fontSize: 11 },
      },
      {
        scale: true,
        gridIndex: 1,
        name: "成交量",
        nameTextStyle: { fontSize: 10, color: "#9ca3af" },
        splitLine: { show: false },
        axisLabel: { show: false },
      },
      {
        scale: true,
        gridIndex: 2,
        name: "MACD",
        nameTextStyle: { fontSize: 10, color: "#9ca3af" },
        splitLine: { lineStyle: { color: "#eef1f5" } },
        axisLabel: { fontSize: 10 },
      },
      {
        scale: true,
        gridIndex: 3,
        name: "RSI",
        min: 0,
        max: 100,
        nameTextStyle: { fontSize: 10, color: "#9ca3af" },
        splitLine: { lineStyle: { color: "#eef1f5" } },
        axisLabel: { fontSize: 10 },
      },
    ],
    dataZoom: [
      {
        type: "inside",
        xAxisIndex: [0, 1, 2, 3],
        start,
        end: 100,
      },
      {
        type: "slider",
        xAxisIndex: [0, 1, 2, 3],
        bottom: 6,
        height: 18,
        start,
        end: 100,
      },
    ],
    series: [
      {
        name: "K线",
        type: "candlestick",
        data: candles,
        itemStyle: {
          color: UP_COLOR,
          color0: DOWN_COLOR,
          borderColor: UP_COLOR,
          borderColor0: DOWN_COLOR,
        },
        markLine: {
          symbol: "none",
          silent: true,
          data: markLines,
        },
      },
      {
        name: "MA5",
        type: "line",
        data: data.indicators.ma.ma5,
        showSymbol: false,
        lineStyle: { width: 1, color: "#f59e0b" },
        itemStyle: { color: "#f59e0b" },
      },
      {
        name: "MA10",
        type: "line",
        data: data.indicators.ma.ma10,
        showSymbol: false,
        lineStyle: { width: 1, color: "#8b5cf6" },
        itemStyle: { color: "#8b5cf6" },
      },
      {
        name: "MA20",
        type: "line",
        data: data.indicators.ma.ma20,
        showSymbol: false,
        lineStyle: { width: 1.2, color: "#1677ff" },
        itemStyle: { color: "#1677ff" },
      },
      {
        name: "MA60",
        type: "line",
        data: data.indicators.ma.ma60,
        showSymbol: false,
        lineStyle: { width: 1.2, color: "#10b981" },
        itemStyle: { color: "#10b981" },
      },
      {
        name: "BOLL上轨",
        type: "line",
        data: data.indicators.boll.upper,
        showSymbol: false,
        lineStyle: { width: 1, type: "dashed", color: "#94a3b8" },
        itemStyle: { color: "#94a3b8" },
      },
      {
        name: "BOLL下轨",
        type: "line",
        data: data.indicators.boll.lower,
        showSymbol: false,
        lineStyle: { width: 1, type: "dashed", color: "#94a3b8" },
        itemStyle: { color: "#94a3b8" },
      },
      {
        name: "成交量",
        type: "bar",
        xAxisIndex: 1,
        yAxisIndex: 1,
        data: volume,
      },
      {
        name: "量MA5",
        type: "line",
        xAxisIndex: 1,
        yAxisIndex: 1,
        data: data.indicators.volMa.ma5,
        showSymbol: false,
        lineStyle: { width: 1, color: "#f59e0b" },
        itemStyle: { color: "#f59e0b" },
      },
      {
        name: "MACD",
        type: "bar",
        xAxisIndex: 2,
        yAxisIndex: 2,
        data: macdHist,
      },
      {
        name: "DIF",
        type: "line",
        xAxisIndex: 2,
        yAxisIndex: 2,
        data: data.indicators.macd.diff,
        showSymbol: false,
        lineStyle: { width: 1, color: "#1677ff" },
        itemStyle: { color: "#1677ff" },
      },
      {
        name: "DEA",
        type: "line",
        xAxisIndex: 2,
        yAxisIndex: 2,
        data: data.indicators.macd.dea,
        showSymbol: false,
        lineStyle: { width: 1, color: "#f59e0b" },
        itemStyle: { color: "#f59e0b" },
      },
      {
        name: "RSI",
        type: "line",
        xAxisIndex: 3,
        yAxisIndex: 3,
        data: data.indicators.rsi,
        showSymbol: false,
        lineStyle: { width: 1.2, color: "#8b5cf6" },
        itemStyle: { color: "#8b5cf6" },
        markLine: {
          symbol: "none",
          silent: true,
          label: { show: false },
          data: [
            { yAxis: 70, lineStyle: { color: "#f87171", type: "dashed" } },
            { yAxis: 30, lineStyle: { color: "#34d399", type: "dashed" } },
          ],
        },
      },
    ],
  };
}

function SignalCard({
  signal,
  loading,
  error,
}: {
  signal?: StockSignal;
  loading: boolean;
  error?: unknown;
}) {
  if (error) {
    return (
      <Card size="small" title="交易信号">
        <Alert
          type="error"
          showIcon
          message="信号加载失败"
          description={error instanceof Error ? error.message : String(error)}
        />
      </Card>
    );
  }
  if (loading || !signal) {
    return (
      <Card size="small" title="交易信号">
        <Spin tip="正在计算信号" />
      </Card>
    );
  }

  return (
    <Card
      size="small"
      title="交易信号"
      extra={<Tag color={SIGNAL_COLORS[signal.signal] ?? "default"}>{signal.signalLabel}</Tag>}
    >
      <Space size={16} align="baseline" wrap>
        <span>
          综合评分{" "}
          <strong style={{ fontSize: 24, fontVariantNumeric: "tabular-nums" }}>
            {signal.score.toFixed(1)}
          </strong>
        </span>
        <span style={{ color: "#6b7280", fontSize: 12 }}>
          置信度 {signal.confidence.toFixed(1)}
        </span>
      </Space>
      <Descriptions
        size="small"
        column={2}
        style={{ marginTop: 12 }}
        items={[
          {
            key: "direction",
            label: "方向",
            children: DIRECTION_LABELS[signal.direction] ?? signal.direction,
          },
          {
            key: "state",
            label: "市场状态",
            children: STATE_LABELS[signal.marketState] ?? signal.marketState,
          },
          {
            key: "daily",
            label: "日线评分",
            children: signal.dailyScore.toFixed(1),
          },
          {
            key: "weekly",
            label: "周线评分",
            children: signal.weeklyScore.toFixed(1),
          },
          {
            key: "close",
            label: "收盘",
            children: signal.close.toFixed(2),
          },
          {
            key: "pct",
            label: "涨跌幅",
            children: (
              <span className={signal.pctChg > 0 ? "up" : signal.pctChg < 0 ? "down" : "flat"}>
                {signal.pctChg > 0 ? "+" : ""}
                {signal.pctChg.toFixed(2)}%
              </span>
            ),
          },
        ]}
      />
      {signal.riskFlags.length > 0 ? (
        <Space size={6} wrap style={{ marginTop: 10 }}>
          {signal.riskFlags.map((flag) => (
            <Tag key={flag} color="orange">
              {flag}
            </Tag>
          ))}
        </Space>
      ) : null}
    </Card>
  );
}

function PlanCard({ plan }: { plan?: SignalTradePlan }) {
  if (!plan) return null;
  return (
    <Card
      size="small"
      title={`交易计划（${plan.entryType === "breakout" ? "突破型" : "回踩型"}）`}
      extra={
        <Tag color={plan.executable ? "green" : "default"}>
          {plan.executable ? "可执行" : "暂不可执行"}
        </Tag>
      }
    >
      <Descriptions
        size="small"
        column={2}
        items={[
          {
            key: "entry",
            label: "入场区间",
            children: `${plan.entryLow.toFixed(2)} - ${plan.entryHigh.toFixed(2)}`,
          },
          { key: "stop", label: "止损", children: plan.stopLoss.toFixed(2) },
          { key: "t1", label: "目标 1", children: plan.target1.toFixed(2) },
          { key: "t2", label: "目标 2", children: plan.target2.toFixed(2) },
          { key: "rr1", label: "盈亏比 1", children: plan.rr1.toFixed(2) },
          { key: "rr2", label: "盈亏比 2", children: plan.rr2.toFixed(2) },
          {
            key: "position",
            label: "建议仓位",
            children: `${plan.positionPct.toFixed(1)}%`,
          },
          { key: "shares", label: "建议股数", children: `${plan.shares} 股` },
        ]}
      />
      <div style={{ marginTop: 8, color: "#6b7280", fontSize: 12 }}>
        {plan.executionNote}
      </div>
    </Card>
  );
}

function EvidenceRow({ label, group }: { label: string; group: EvidenceGroup }) {
  return (
    <div style={{ marginBottom: 12 }}>
      <div
        style={{
          display: "flex",
          justifyContent: "space-between",
          fontSize: 13,
          marginBottom: 2,
        }}
      >
        <span>
          {label}
          <Tag
            bordered={false}
            color={biasColor(group.bias)}
            style={{ marginLeft: 8 }}
          >
            {BIAS_LABELS[group.bias] ?? group.bias}
          </Tag>
        </span>
        <span style={{ color: "#6b7280", fontSize: 12 }}>{group.score.toFixed(1)}</span>
      </div>
      <Progress
        percent={Math.round(group.score)}
        size="small"
        showInfo={false}
        strokeColor={biasColor(group.bias)}
      />
      <ul style={{ margin: "4px 0 0", paddingLeft: 18, color: "#6b7280", fontSize: 12 }}>
        {group.reasons.map((reason) => (
          <li key={reason}>{reason}</li>
        ))}
      </ul>
    </div>
  );
}

function EvidenceCard({ signal }: { signal?: StockSignal }) {
  if (!signal) return null;
  return (
    <Card size="small" title="证据链">
      <EvidenceRow label="技术面" group={signal.evidence.technical} />
      <EvidenceRow label="资金面" group={signal.evidence.capital} />
      <EvidenceRow label="情绪面" group={signal.evidence.sentiment} />
      <div style={{ borderTop: "1px dashed #eef1f5", paddingTop: 8, marginTop: 4 }}>
        <div style={{ color: "#6b7280", fontSize: 12, marginBottom: 6 }}>
          评分分解（日线）
        </div>
        {signal.scoreBreakdown.map((part) => (
          <div
            key={part.factor}
            style={{
              display: "flex",
              justifyContent: "space-between",
              fontSize: 12,
              padding: "2px 0",
            }}
          >
            <span>
              {part.label}
              <span style={{ color: "#9ca3af", marginLeft: 6 }}>
                {(part.weight * 100).toFixed(0)}%
              </span>
            </span>
            <span style={{ color: part.contribution >= 0 ? UP_COLOR : DOWN_COLOR }}>
              {part.contribution >= 0 ? "+" : ""}
              {part.contribution.toFixed(1)}
            </span>
          </div>
        ))}
      </div>
      <ul style={{ margin: "8px 0 0", paddingLeft: 18, color: "#9ca3af", fontSize: 12 }}>
        {signal.constraints.map((item) => (
          <li key={item}>{item}</li>
        ))}
      </ul>
    </Card>
  );
}

export default function KlinePage() {
  const [code, setCode] = useState("600001.SH");
  const [period, setPeriod] = useState<"daily" | "weekly" | "monthly">("daily");
  const [adjust, setAdjust] = useState<"qfq" | "hfq" | "raw">("qfq");
  const [searching, setSearching] = useState(false);
  const containerRef = useRef<HTMLDivElement | null>(null);
  const chartRef = useRef<echarts.ECharts | null>(null);

  const klineQuery = useQuery({
    queryKey: ["kline", code, period, adjust],
    queryFn: () => api.kline(code, { period, adjust, limit: 250 }),
    staleTime: 10 * 60 * 1000,
    retry: 1,
  });

  const signalQuery = useQuery({
    queryKey: ["signal", code],
    queryFn: () => api.signal(code),
    staleTime: 10 * 60 * 1000,
    retry: 1,
  });

  const kline = klineQuery.data?.data;
  const signal = signalQuery.data?.data;

  useEffect(() => {
    if (!containerRef.current) return;
    const chart = echarts.init(containerRef.current);
    chartRef.current = chart;
    const observer = new ResizeObserver(() => chart.resize());
    observer.observe(containerRef.current);
    return () => {
      observer.disconnect();
      chart.dispose();
      chartRef.current = null;
    };
  }, []);

  useEffect(() => {
    const chart = chartRef.current;
    if (!chart || !kline || kline.bars.length === 0) return;
    chart.setOption(buildOption(kline, signal?.tradePlan), true);
  }, [kline, signal]);

  const periodLabel = useMemo(
    () => PERIOD_OPTIONS.find((item) => item.value === period)?.label ?? period,
    [period],
  );

  async function handleSearch(value: string) {
    const keyword = value.trim();
    if (!keyword) return;
    if (/^\d{6}(\.(SH|SZ|BJ))?$/i.test(keyword)) {
      setCode(keyword.toUpperCase());
      return;
    }
    try {
      setSearching(true);
      const result = await api.tickers({ search: keyword, limit: 1 });
      const hit = result.data.items[0];
      if (hit) {
        setCode(hit.tsCode);
      } else {
        message.warning("未找到匹配的股票");
      }
    } catch (error) {
      message.error(error instanceof Error ? error.message : "搜索失败");
    } finally {
      setSearching(false);
    }
  }

  if (klineQuery.error) {
    return (
      <Alert
        type="error"
        showIcon
        message="K 线数据加载失败"
        description={
          klineQuery.error instanceof Error
            ? klineQuery.error.message
            : String(klineQuery.error)
        }
        action={<Button onClick={() => klineQuery.refetch()}>重试</Button>}
      />
    );
  }

  const envelope = klineQuery.data;

  return (
    <div className="page">
      <div className="page-header">
        <div>
          <h2 className="page-title">K 线与交易信号</h2>
          <p className="page-subtitle">
            {kline
              ? `${kline.name} ${kline.tsCode} · ${periodLabel} · ${
                  ADJUST_OPTIONS.find((item) => item.value === adjust)?.label ?? adjust
                } · ${kline.bars.length} 根 K 线`
              : "加载中"}
            {envelope ? ` · 来源 ${envelope.source}` : ""}
          </p>
        </div>
        <Space>
          {envelope?.stale ? <Tag color="red">数据滞后</Tag> : null}
          <Button
            onClick={() => {
              klineQuery.refetch();
              signalQuery.refetch();
            }}
            loading={klineQuery.isFetching || signalQuery.isFetching}
          >
            刷新
          </Button>
        </Space>
      </div>

      {envelope && envelope.warnings.length > 0 ? (
        <Alert
          type="warning"
          showIcon
          message="数据提示"
          description={
            <ul style={{ margin: 0, paddingLeft: 18 }}>
              {envelope.warnings.map((warning) => (
                <li key={warning}>{warning}</li>
              ))}
            </ul>
          }
        />
      ) : null}

      <Card size="small">
        <Space size={12} wrap>
          <Input.Search
            allowClear
            placeholder="代码 / 名称"
            style={{ width: 220 }}
            loading={searching}
            onSearch={handleSearch}
          />
          <Select
            value={period}
            options={PERIOD_OPTIONS}
            style={{ width: 110 }}
            onChange={(value) => setPeriod(value)}
          />
          <Select
            value={adjust}
            options={ADJUST_OPTIONS}
            style={{ width: 120 }}
            onChange={(value) => setAdjust(value)}
          />
          <span style={{ color: "#9ca3af", fontSize: 12 }}>
            数据截至 {kline?.bars[kline.bars.length - 1]?.date ?? "-"}
          </span>
        </Space>
      </Card>

      <div className="kline-layout">
        <Card
          size="small"
          title="行情走势"
          extra={<Tag bordered={false}>{periodLabel}</Tag>}
        >
          <div className="kline-chart-wrap">
            <div ref={containerRef} className="kline-chart" />
            {klineQuery.isLoading ? (
              <div className="kline-chart-mask">
                <Spin tip="正在加载 K 线" />
              </div>
            ) : !kline || kline.bars.length === 0 ? (
              <div className="kline-chart-mask">
                <Empty description="暂无 K 线数据" />
              </div>
            ) : null}
          </div>
          {kline && kline.bars.length > 0 && kline.bars.length < 60 ? (
            <div className="footer-note">数据不足 60 根 K 线，长周期指标仅供参考</div>
          ) : null}
        </Card>

        <div className="kline-side">
          <SignalCard
            signal={signal}
            loading={signalQuery.isLoading}
            error={signalQuery.error}
          />
          <PlanCard plan={signal?.tradePlan} />
          <EvidenceCard signal={signal} />
        </div>
      </div>

      <div className="footer-note">仅供研究，不构成投资建议</div>
    </div>
  );
}
