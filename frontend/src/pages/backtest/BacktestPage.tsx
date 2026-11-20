import { useQuery, useQueryClient } from "@tanstack/react-query";
import * as echarts from "echarts";
import type { EChartsOption } from "echarts";
import {
  Alert,
  Button,
  Card,
  Descriptions,
  Empty,
  Input,
  InputNumber,
  Select,
  Space,
  Spin,
  Table,
  Tag,
  Typography,
  message,
} from "antd";
import type { ColumnsType } from "antd/es/table";
import { useEffect, useRef, useState } from "react";

import { api } from "../../api/client";
import type {
  BacktestAudit,
  BacktestAuditTrial,
  BacktestMetrics,
  BacktestNavPoint,
  BacktestTrade,
} from "../../api/types";

const STRATEGY_OPTIONS = [
  { value: "ma_cross", label: "双均线（ma_cross）" },
  { value: "radar_topk", label: "雷达选股（radar_topk）" },
  { value: "signal_rule", label: "信号评分（signal_rule）" },
];

const DEFAULT_PARAMS: Record<string, string> = {
  ma_cross: '{"fast": 5, "slow": 20}',
  radar_topk: '{"top_k": 10, "min_score": 50}',
  signal_rule: '{"min_score": 30}',
};

const DEFAULT_THRESHOLDS = '{"pbo_max": 0.5, "dsr_min": 0.95, "cpcv_positive_min": 0.5}';

const STATUS_LABELS: Record<string, string> = {
  queued: "排队中",
  running: "运行中",
  completed: "已完成",
  failed: "失败",
};

const STATUS_COLORS: Record<string, string> = {
  queued: "default",
  running: "processing",
  completed: "green",
  failed: "red",
};

const ACTION_LABELS: Record<string, string> = {
  buy: "买入",
  sell: "卖出",
};

const TRADE_STATUS_LABELS: Record<string, string> = {
  filled: "成交",
  rejected: "拒单",
  expired: "过期",
};

function fmtPct(value: unknown, digits = 2): string {
  const num = Number(value);
  return Number.isFinite(num) ? `${(num * 100).toFixed(digits)}%` : "-";
}

function fmtNum(value: unknown, digits = 2): string {
  const num = Number(value);
  return Number.isFinite(num) ? num.toFixed(digits) : "-";
}

function fmtMoney(value: unknown): string {
  const num = Number(value);
  return Number.isFinite(num)
    ? num.toLocaleString("zh-CN", { maximumFractionDigits: 0 })
    : "-";
}

function pnlClass(value: number): string {
  return value > 0 ? "up" : value < 0 ? "down" : "flat";
}

function buildNavOption(points: BacktestNavPoint[]): EChartsOption {
  const dates = points.map((point) => point.date);
  const nav = points.map((point) => point.nav);
  const drawdown = points.map((point) => point.drawdown);

  return {
    animation: false,
    tooltip: { trigger: "axis", confine: true },
    legend: {
      top: 0,
      right: 8,
      itemWidth: 12,
      itemHeight: 8,
      textStyle: { fontSize: 11 },
      data: ["净值", "回撤"],
    },
    grid: { left: 64, right: 64, top: 32, bottom: 32 },
    xAxis: {
      type: "category",
      data: dates,
      boundaryGap: false,
      axisLabel: { fontSize: 11 },
      axisLine: { lineStyle: { color: "#d1d5db" } },
      axisTick: { show: false },
    },
    yAxis: [
      {
        type: "value",
        scale: true,
        name: "净值",
        nameTextStyle: { fontSize: 10, color: "#9ca3af" },
        splitLine: { lineStyle: { color: "#eef1f5" } },
        axisLabel: { fontSize: 11 },
      },
      {
        type: "value",
        scale: true,
        name: "回撤",
        nameTextStyle: { fontSize: 10, color: "#9ca3af" },
        splitLine: { show: false },
        axisLabel: {
          fontSize: 11,
          formatter: (value: number | string) => `${(Number(value) * 100).toFixed(0)}%`,
        },
      },
    ],
    series: [
      {
        name: "净值",
        type: "line",
        data: nav,
        showSymbol: false,
        lineStyle: { width: 1.6, color: "#1677ff" },
        itemStyle: { color: "#1677ff" },
        areaStyle: { opacity: 0.08 },
      },
      {
        name: "回撤",
        type: "line",
        yAxisIndex: 1,
        data: drawdown,
        showSymbol: false,
        lineStyle: { width: 1, color: "#f87171" },
        itemStyle: { color: "#f87171" },
      },
    ],
  };
}

function NavChart({ points }: { points: BacktestNavPoint[] }) {
  const containerRef = useRef<HTMLDivElement | null>(null);
  const chartRef = useRef<echarts.ECharts | null>(null);

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
    if (chartRef.current && points.length > 0) {
      chartRef.current.setOption(buildNavOption(points), true);
    }
  }, [points]);

  return <div ref={containerRef} style={{ width: "100%", height: 360 }} />;
}

function MetricCard({
  label,
  value,
  delta,
  tone,
}: {
  label: string;
  value: string;
  delta?: string;
  tone?: string;
}) {
  return (
    <div className="metric-card">
      <div className="metric-label">{label}</div>
      <div className={`metric-value ${tone ?? ""}`}>{value}</div>
      {delta ? <div className="metric-delta">{delta}</div> : null}
    </div>
  );
}

function MetricsPanel({ metrics }: { metrics: BacktestMetrics }) {
  const years = Object.entries(metrics.yearly_returns ?? {});

  return (
    <Space direction="vertical" size={12} style={{ width: "100%" }}>
      <div className="metric-grid">
        <MetricCard
          label="总收益"
          value={fmtPct(metrics.total_return)}
          delta={`期末权益 ${fmtMoney(metrics.final_value)}`}
          tone={pnlClass(metrics.total_return)}
        />
        <MetricCard
          label="年化收益"
          value={fmtPct(metrics.annual_return)}
          delta={`年化波动 ${fmtPct(metrics.annual_volatility)}`}
          tone={pnlClass(metrics.annual_return)}
        />
        <MetricCard
          label="最大回撤"
          value={fmtPct(metrics.max_drawdown)}
          delta={`持续 ${metrics.max_drawdown_days ?? 0} 个交易日`}
          tone="down"
        />
        <MetricCard
          label="Sharpe"
          value={fmtNum(metrics.sharpe)}
          delta={`Sortino ${fmtNum(metrics.sortino)} · Calmar ${fmtNum(metrics.calmar)}`}
        />
        <MetricCard
          label="胜率"
          value={fmtPct(metrics.win_rate)}
          delta={`盈亏比 ${fmtNum(metrics.profit_loss_ratio)} · 盈利因子 ${fmtNum(metrics.profit_factor)}`}
        />
        <MetricCard
          label="交易次数"
          value={`${metrics.trade_count ?? 0}`}
          delta={`成交 ${metrics.filled_count ?? 0} · 拒单 ${metrics.rejected_count ?? 0} · 过期 ${metrics.expired_count ?? 0}`}
        />
      </div>

      <Descriptions
        size="small"
        column={4}
        bordered
        items={[
          {
            key: "fees",
            label: "总费用",
            children: `${fmtMoney(metrics.total_fees)} 元（占收益 ${fmtPct(metrics.fee_ratio)}）`,
          },
          {
            key: "holding",
            label: "平均持仓",
            children: `${fmtNum(metrics.avg_holding_days, 1)} 天`,
          },
          {
            key: "turnover",
            label: "换手率",
            children: fmtPct(metrics.turnover_rate),
          },
          {
            key: "benchmark",
            label: "基准收益",
            children: fmtPct(metrics.benchmark_return),
          },
          {
            key: "excess",
            label: "超额收益",
            children: (
              <span className={pnlClass(metrics.excess_return)}>
                {fmtPct(metrics.excess_return)}
              </span>
            ),
          },
          {
            key: "ir",
            label: "信息比率",
            children: fmtNum(metrics.information_ratio),
          },
          {
            key: "alpha",
            label: "Alpha / Beta",
            children: `${fmtNum(metrics.alpha, 3)} / ${fmtNum(metrics.beta, 2)}`,
          },
          {
            key: "deferrals",
            label: "停牌顺延",
            children: `${metrics.suspension_deferrals ?? 0} 次`,
          },
        ]}
      />

      {years.length > 0 ? (
        <Descriptions
          size="small"
          column={6}
          bordered
          items={years.map(([year, value]) => ({
            key: year,
            label: year,
            children: (
              <span className={pnlClass(value)}>{fmtPct(value)}</span>
            ),
          }))}
        />
      ) : null}
    </Space>
  );
}

const tradeColumns: ColumnsType<BacktestTrade> = [
  { title: "成交日", dataIndex: "date", key: "date", width: 110 },
  { title: "代码", dataIndex: "ts_code", key: "ts_code", width: 110 },
  {
    title: "方向",
    dataIndex: "action",
    key: "action",
    width: 70,
    render: (value: string) => (
      <Tag color={value === "buy" ? "red" : "green"}>
        {ACTION_LABELS[value] ?? value}
      </Tag>
    ),
  },
  {
    title: "状态",
    dataIndex: "status",
    key: "status",
    width: 80,
    render: (value: string) =>
      value === "filled" ? (
        <Tag color="blue">{TRADE_STATUS_LABELS[value] ?? value}</Tag>
      ) : (
        <Tag>{TRADE_STATUS_LABELS[value] ?? value}</Tag>
      ),
  },
  {
    title: "股数",
    dataIndex: "shares",
    key: "shares",
    width: 90,
    align: "right",
  },
  {
    title: "价格",
    dataIndex: "price",
    key: "price",
    width: 90,
    align: "right",
    render: (value: number) => fmtNum(value),
  },
  {
    title: "金额",
    dataIndex: "amount",
    key: "amount",
    width: 110,
    align: "right",
    render: (value: number) => fmtMoney(value),
  },
  {
    title: "费用",
    dataIndex: "commission",
    key: "fees",
    width: 100,
    align: "right",
    render: (_, item) =>
      fmtNum(
        (item.commission ?? 0) + (item.stamp_duty ?? 0) + (item.transfer_fee ?? 0),
      ),
  },
  {
    title: "盈亏",
    dataIndex: "profit",
    key: "profit",
    width: 100,
    align: "right",
    render: (value: number) => (
      <span className={pnlClass(value ?? 0)}>{fmtMoney(value)}</span>
    ),
  },
  {
    title: "拒单 / 备注",
    key: "reason",
    render: (_, item) => (
      <span style={{ color: "#9ca3af", fontSize: 12 }}>
        {item.rejection_reason || item.reason || "-"}
      </span>
    ),
  },
];

const auditTrialColumns: ColumnsType<BacktestAuditTrial> = [
  {
    title: "参数",
    dataIndex: "params",
    key: "params",
    render: (value: Record<string, unknown>) => (
      <span style={{ fontSize: 12 }}>{JSON.stringify(value)}</span>
    ),
  },
  {
    title: "Sharpe",
    dataIndex: "sharpe",
    key: "sharpe",
    width: 90,
    align: "right",
    sorter: (a, b) => a.sharpe - b.sharpe,
    render: (value: number) => fmtNum(value),
  },
  {
    title: "总收益",
    dataIndex: "total_return",
    key: "total_return",
    width: 100,
    align: "right",
    render: (value: number) => (
      <span className={pnlClass(value)}>{fmtPct(value)}</span>
    ),
  },
  {
    title: "最大回撤",
    dataIndex: "max_drawdown",
    key: "max_drawdown",
    width: 100,
    align: "right",
    render: (value: number) => fmtPct(value),
  },
  {
    title: "交易次数",
    dataIndex: "trade_count",
    key: "trade_count",
    width: 90,
    align: "right",
  },
];

export default function BacktestPage() {
  const queryClient = useQueryClient();

  const [strategy, setStrategy] = useState("ma_cross");
  const [startDate, setStartDate] = useState("2026-04-01");
  const [endDate, setEndDate] = useState("2026-11-18");
  const [initialCash, setInitialCash] = useState(1_000_000);
  const [maxPositions, setMaxPositions] = useState(10);
  const [maxPosition, setMaxPosition] = useState(0.1);
  const [paramsText, setParamsText] = useState(DEFAULT_PARAMS.ma_cross);
  const [runId, setRunId] = useState<string>();
  const [submitting, setSubmitting] = useState(false);
  const [auditing, setAuditing] = useState(false);
  const [localAudit, setLocalAudit] = useState<BacktestAudit | null | undefined>();
  const [gridText, setGridText] = useState('{"fast": [5, 10], "slow": [20, 30]}');
  const [thresholdText, setThresholdText] = useState(DEFAULT_THRESHOLDS);

  const statusQuery = useQuery({
    queryKey: ["backtest-status", runId],
    queryFn: () => api.backtestStatus(runId as string),
    enabled: Boolean(runId),
    refetchInterval: (query) => {
      const status = query.state.data?.data.status;
      return status === "completed" || status === "failed" ? false : 1000;
    },
  });

  const status = statusQuery.data?.data.status;
  const completed = status === "completed";

  const detailQuery = useQuery({
    queryKey: ["backtest-detail", runId],
    queryFn: () => api.backtestDetail(runId as string),
    enabled: Boolean(runId) && completed,
    staleTime: Infinity,
  });

  const navQuery = useQuery({
    queryKey: ["backtest-nav", runId],
    queryFn: () => api.backtestNav(runId as string),
    enabled: Boolean(runId) && completed,
    staleTime: Infinity,
  });

  const tradesQuery = useQuery({
    queryKey: ["backtest-trades", runId],
    queryFn: () => api.backtestTrades(runId as string),
    enabled: Boolean(runId) && completed,
    staleTime: Infinity,
  });

  const auditQuery = useQuery({
    queryKey: ["backtest-audit", runId],
    queryFn: () => api.backtestAudit(runId as string),
    enabled: Boolean(runId) && completed,
    retry: false,
    staleTime: Infinity,
  });

  const detail = detailQuery.data?.data;
  const metrics = detail?.metrics;
  const navPoints = navQuery.data?.data.items ?? [];
  const trades = tradesQuery.data?.data.items ?? [];
  const audit = localAudit !== undefined ? localAudit : auditQuery.data?.data.audit;

  function handleStrategyChange(value: string) {
    setStrategy(value);
    setParamsText(DEFAULT_PARAMS[value] ?? "{}");
    if (value === "ma_cross") {
      setGridText('{"fast": [5, 10], "slow": [20, 30]}');
    } else if (value === "radar_topk") {
      setGridText('{"top_k": [10, 20]}');
    } else {
      setGridText('{"min_score": [30, 50]}');
    }
  }

  async function handleSubmit() {
    let params: Record<string, unknown>;
    try {
      params = JSON.parse(paramsText || "{}");
    } catch {
      message.error("策略参数不是合法 JSON");
      return;
    }
    try {
      setSubmitting(true);
      const result = await api.runBacktest({
        startDate,
        endDate,
        strategy,
        params,
        initialCash,
        maxPositions,
        maxPosition,
      });
      setRunId(result.data.runId);
      setLocalAudit(undefined);
      message.success(`回测已提交：${result.data.runId}`);
    } catch (error) {
      message.error(error instanceof Error ? error.message : "回测提交失败");
    } finally {
      setSubmitting(false);
    }
  }

  async function handleAudit() {
    if (!runId) return;
    let paramGrid: Record<string, unknown[]>;
    let thresholds: Record<string, number>;
    try {
      paramGrid = JSON.parse(gridText || "{}");
    } catch {
      message.error("参数网格不是合法 JSON");
      return;
    }
    try {
      thresholds = JSON.parse(thresholdText || "{}");
    } catch {
      message.error("阈值不是合法 JSON");
      return;
    }
    try {
      setAuditing(true);
      const result = await api.runBacktestAudit(runId, {
        paramGrid,
        thresholds,
      });
      queryClient.setQueryData(["backtest-audit", runId], result);
      setLocalAudit(result.data.audit);
      message.success(
        result.data.audit.verdict === "accepted" ? "审计通过" : "审计未通过",
      );
    } catch (error) {
      message.error(error instanceof Error ? error.message : "审计执行失败");
    } finally {
      setAuditing(false);
    }
  }

  const auditCard = audit ? (
    <AuditResult audit={audit} onReset={() => setLocalAudit(null)} />
  ) : (
    <Space direction="vertical" size={10} style={{ width: "100%" }}>
      <div>
        <div className="metric-label" style={{ marginBottom: 4 }}>
          参数网格 JSON
        </div>
        <Input.TextArea
          rows={2}
          value={gridText}
          onChange={(event) => setGridText(event.target.value)}
        />
      </div>
      <div>
        <div className="metric-label" style={{ marginBottom: 4 }}>
          判定阈值 JSON
        </div>
        <Input.TextArea
          rows={2}
          value={thresholdText}
          onChange={(event) => setThresholdText(event.target.value)}
        />
      </div>
      <Button type="primary" loading={auditing} onClick={handleAudit}>
        运行参数审计
      </Button>
      <div className="footer-note" style={{ textAlign: "left" }}>
        在同一区间上运行参数网格，计算 PBO / CPCV / DSR 并给出 verdict
      </div>
    </Space>
  );

  return (
    <div className="page">
      <div className="page-header">
        <div>
          <h2 className="page-title">回测</h2>
          <p className="page-subtitle">
            T+1 开盘成交 · 100 股整手 · 费用与滑点内置
            {runId ? ` · ${runId}` : ""}
          </p>
        </div>
        <Space>
          {status ? (
            <Tag color={STATUS_COLORS[status] ?? "default"}>
              {STATUS_LABELS[status] ?? status}
            </Tag>
          ) : null}
        </Space>
      </div>

      <Card size="small" title="回测参数">
        <Space size={12} wrap align="end">
          <div>
            <div className="metric-label">策略</div>
            <Select
              value={strategy}
              options={STRATEGY_OPTIONS}
              style={{ width: 200 }}
              onChange={handleStrategyChange}
            />
          </div>
          <div>
            <div className="metric-label">开始日期</div>
            <Input
              value={startDate}
              style={{ width: 130 }}
              onChange={(event) => setStartDate(event.target.value)}
            />
          </div>
          <div>
            <div className="metric-label">结束日期</div>
            <Input
              value={endDate}
              style={{ width: 130 }}
              onChange={(event) => setEndDate(event.target.value)}
            />
          </div>
          <div>
            <div className="metric-label">初始资金</div>
            <InputNumber
              value={initialCash}
              min={10_000}
              step={100_000}
              style={{ width: 140 }}
              onChange={(value) => setInitialCash(value ?? 1_000_000)}
            />
          </div>
          <div>
            <div className="metric-label">最大持仓数</div>
            <InputNumber
              value={maxPositions}
              min={1}
              max={100}
              style={{ width: 110 }}
              onChange={(value) => setMaxPositions(value ?? 10)}
            />
          </div>
          <div>
            <div className="metric-label">单票上限</div>
            <InputNumber
              value={maxPosition}
              min={0.01}
              max={1}
              step={0.05}
              style={{ width: 110 }}
              onChange={(value) => setMaxPosition(value ?? 0.1)}
            />
          </div>
          <div style={{ flex: 1, minWidth: 260 }}>
            <div className="metric-label">策略参数 JSON</div>
            <Input
              value={paramsText}
              onChange={(event) => setParamsText(event.target.value)}
            />
          </div>
          <Button type="primary" loading={submitting} onClick={handleSubmit}>
            开始回测
          </Button>
        </Space>
      </Card>

      {status === "failed" ? (
        <Alert
          type="error"
          showIcon
          message="回测失败"
          description={statusQuery.data?.data.error ?? "未知错误"}
        />
      ) : null}

      {status && !completed && status !== "failed" ? (
        <Card size="small">
          <Space align="center">
            <Spin />
            <span style={{ color: "#6b7280", fontSize: 13 }}>
              回测{STATUS_LABELS[status] ?? status}，正在轮询状态
            </span>
          </Space>
        </Card>
      ) : null}

      {completed && metrics ? (
        <Card size="small" title="绩效指标">
          <MetricsPanel metrics={metrics} />
        </Card>
      ) : null}

      {completed ? (
        <Card size="small" title="净值曲线">
          {navPoints.length === 0 ? (
            <Empty description="暂无净值数据" />
          ) : (
            <NavChart points={navPoints} />
          )}
        </Card>
      ) : null}

      {completed ? (
        <Card
          size="small"
          title="成交记录"
          extra={
            <Typography.Text type="secondary" style={{ fontSize: 12 }}>
              共 {trades.length} 条
            </Typography.Text>
          }
        >
          <Table<BacktestTrade>
            size="small"
            rowKey={(item) => `${item.date}-${item.ts_code}-${item.action}-${item.shares}-${item.status}`}
            columns={tradeColumns}
            dataSource={trades}
            pagination={{ pageSize: 20, size: "small" }}
            locale={{ emptyText: "暂无成交记录" }}
          />
        </Card>
      ) : null}

      {completed ? (
        <Card size="small" title="参数敏感性审计（CPCV / PBO / DSR）">
          {auditCard}
        </Card>
      ) : null}

      <div className="footer-note">仅供研究，不构成投资建议</div>
    </div>
  );
}

function AuditResult({
  audit,
  onReset,
}: {
  audit: BacktestAudit;
  onReset: () => void;
}) {
  const accepted = audit.verdict === "accepted";
  const ordered = [...audit.trials.details].sort((a, b) => b.sharpe - a.sharpe);

  return (
    <Space direction="vertical" size={12} style={{ width: "100%" }}>
      <Space size={8} wrap>
        <Tag color={accepted ? "green" : "red"}>
          {accepted ? "审计通过" : "审计未通过"}
        </Tag>
        <span style={{ color: "#9ca3af", fontSize: 12 }}>
          生成于 {audit.created_at} · 观测参数 {audit.observed.label}
        </span>
        <Button size="small" onClick={onReset}>
          重新审计
        </Button>
      </Space>

      <div className="metric-grid">
        <MetricCard
          label="PBO"
          value={fmtPct(audit.pbo.pbo)}
          delta={`${audit.pbo.combinations} 组切分 · 阈值 ${audit.thresholds.pbo_max ?? "-"}`}
          tone={audit.pbo.pbo > (audit.thresholds.pbo_max ?? 0.5) ? "down" : "up"}
        />
        <MetricCard
          label="CPCV 正收益占比"
          value={fmtPct(audit.cpcv.positive_ratio)}
          delta={`${audit.cpcv.splits} 折 · 均值夏普 ${fmtNum(audit.cpcv.mean_test_sharpe)}`}
          tone={
            audit.cpcv.positive_ratio < (audit.thresholds.cpcv_positive_min ?? 0.5)
              ? "down"
              : "up"
          }
        />
        <MetricCard
          label="DSR"
          value={fmtNum(audit.dsr.dsr, 3)}
          delta={`试验 ${audit.dsr.trials} 次 · 阈值 ${audit.thresholds.dsr_min ?? "-"}`}
          tone={audit.dsr.dsr < (audit.thresholds.dsr_min ?? 0.95) ? "down" : "up"}
        />
        <MetricCard
          label="观测 Sharpe"
          value={fmtNum(audit.observed.sharpe)}
          delta={`紧缩后 ${fmtNum(audit.dsr.haircut_sharpe)} · SR0 ${fmtNum(audit.dsr.sr0)}`}
        />
        <MetricCard
          label="候选参数"
          value={`${audit.trials.count}`}
          delta={`${audit.trials.configs.length} 组配置 · ${audit.observed.trading_days} 个交易日`}
        />
        <MetricCard
          label="CPCV 测试夏普波动"
          value={fmtNum(audit.cpcv.std_test_sharpe)}
          delta={`选中参数 ${Object.keys(audit.cpcv.selected)[0] ?? "-"}`}
        />
      </div>

      {audit.reasons.length > 0 ? (
        <Alert
          type="error"
          showIcon
          message="未通过原因"
          description={
            <ul style={{ margin: 0, paddingLeft: 18 }}>
              {audit.reasons.map((reason) => (
                <li key={reason}>{reason}</li>
              ))}
            </ul>
          }
        />
      ) : null}

      {audit.notes.length > 0 ? (
        <Alert
          type="info"
          showIcon
          message="说明"
          description={
            <ul style={{ margin: 0, paddingLeft: 18 }}>
              {audit.notes.map((note) => (
                <li key={note}>{note}</li>
              ))}
            </ul>
          }
        />
      ) : null}

      <Table<BacktestAuditTrial>
        size="small"
        title={() => <span style={{ fontSize: 13 }}>试验明细</span>}
        rowKey={(item) => JSON.stringify(item.params)}
        columns={auditTrialColumns}
        dataSource={ordered}
        pagination={ordered.length > 10 ? { pageSize: 10, size: "small" } : false}
      />
    </Space>
  );
}
