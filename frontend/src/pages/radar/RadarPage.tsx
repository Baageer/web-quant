import { useQuery } from "@tanstack/react-query";
import {
  Alert,
  Button,
  Card,
  Descriptions,
  Drawer,
  Input,
  Progress,
  Select,
  Space,
  Spin,
  Switch,
  Table,
  Tag,
} from "antd";
import type { ColumnsType } from "antd/es/table";
import { useMemo, useState } from "react";

import { api } from "../../api/client";
import type { RadarDetail, RadarOpportunity } from "../../api/types";

const SIGNAL_COLORS: Record<string, string> = {
  STRONG_BUY: "red",
  BUY: "volcano",
  WATCH: "gold",
  NEUTRAL: "default",
  AVOID: "green",
};

const RISK_COLORS: Record<string, string> = {
  high: "red",
  medium: "orange",
  low: "green",
};

const RISK_LABELS: Record<string, string> = {
  high: "高",
  medium: "中",
  low: "低",
};

const TOPK_OPTIONS = [20, 30, 50, 100].map((value) => ({
  value,
  label: `Top ${value}`,
}));

const MIN_AMOUNT_OPTIONS = [
  { value: 20_000_000, label: "≥ 2000 万" },
  { value: 50_000_000, label: "≥ 5000 万" },
  { value: 100_000_000, label: "≥ 1 亿" },
  { value: 300_000_000, label: "≥ 3 亿" },
];

function pctClass(value: number): string {
  return value > 0 ? "up" : value < 0 ? "down" : "flat";
}

const columns: ColumnsType<RadarOpportunity> = [
  {
    title: "#",
    dataIndex: "rank",
    key: "rank",
    width: 52,
    align: "center",
  },
  {
    title: "名称 / 代码",
    dataIndex: "name",
    key: "name",
    render: (_, item) => (
      <div>
        <div style={{ fontWeight: 600 }}>{item.name}</div>
        <div style={{ color: "#9ca3af", fontSize: 12 }}>{item.tsCode}</div>
      </div>
    ),
  },
  {
    title: "行业",
    dataIndex: "industry",
    key: "industry",
    width: 96,
    render: (value: string | null | undefined) =>
      value ? <Tag bordered={false}>{value}</Tag> : "-",
  },
  {
    title: "收盘",
    dataIndex: "close",
    key: "close",
    width: 84,
    align: "right",
    render: (value: number) => value?.toFixed(2) ?? "-",
  },
  {
    title: "涨跌幅",
    dataIndex: "pctChg",
    key: "pctChg",
    width: 92,
    align: "right",
    render: (value: number) => (
      <span className={pctClass(value)}>
        {value > 0 ? "+" : ""}
        {value.toFixed(2)}%
      </span>
    ),
  },
  {
    title: "成交额（亿）",
    dataIndex: "amount",
    key: "amount",
    width: 106,
    align: "right",
    render: (value: number) => value?.toFixed(2) ?? "-",
  },
  {
    title: "量比",
    dataIndex: "volumeRatio",
    key: "volumeRatio",
    width: 76,
    align: "right",
    render: (value: number | null | undefined) =>
      value == null ? "-" : value.toFixed(2),
  },
  {
    title: "得分",
    dataIndex: "score",
    key: "score",
    width: 84,
    align: "right",
    sorter: (a, b) => a.score - b.score,
    render: (value: number) => (
      <span style={{ fontWeight: 650, fontVariantNumeric: "tabular-nums" }}>
        {value.toFixed(1)}
      </span>
    ),
  },
  {
    title: "置信度",
    dataIndex: "confidence",
    key: "confidence",
    width: 80,
    align: "right",
    render: (value: number) => value.toFixed(1),
  },
  {
    title: "信号",
    dataIndex: "signal",
    key: "signal",
    width: 92,
    render: (_, item) => (
      <Tag color={SIGNAL_COLORS[item.signal] ?? "default"}>{item.signalLabel}</Tag>
    ),
  },
  {
    title: "风险",
    dataIndex: "riskLevel",
    key: "riskLevel",
    width: 72,
    align: "center",
    render: (value: string) => (
      <Tag color={RISK_COLORS[value] ?? "default"}>{RISK_LABELS[value] ?? value}</Tag>
    ),
  },
];

function FactorBreakdown({ detail }: { detail: RadarDetail }) {
  return (
    <div>
      {detail.factors.map((factor) => {
        const color =
          factor.percentile >= 70
            ? "#d4380d"
            : factor.percentile >= 40
              ? "#faad14"
              : "#0f8a5f";
        return (
          <div key={factor.key} style={{ marginBottom: 10 }}>
            <div
              style={{
                display: "flex",
                justifyContent: "space-between",
                fontSize: 13,
                marginBottom: 2,
              }}
            >
              <span>
                {factor.label}
                <span style={{ color: "#9ca3af", marginLeft: 8, fontSize: 12 }}>
                  {factor.valueText}
                </span>
              </span>
              <span style={{ color: "#6b7280", fontSize: 12 }}>
                权重 {(factor.weight * 100).toFixed(0)}% · 贡献{" "}
                {factor.contribution.toFixed(2)}
              </span>
            </div>
            <Progress
              percent={Math.round(factor.percentile)}
              size="small"
              showInfo={false}
              strokeColor={color}
            />
          </div>
        );
      })}
      <div style={{ color: "#9ca3af", fontSize: 12 }}>
        综合得分 = 因子加权 {detail.rawScore.toFixed(2)} − 风险扣分{" "}
        {detail.riskPenalty.toFixed(2)} = {detail.score.toFixed(1)}
      </div>
    </div>
  );
}

export default function RadarPage() {
  const [dateDraft, setDateDraft] = useState("");
  const [date, setDate] = useState("");
  const [topK, setTopK] = useState(50);
  const [minAmount, setMinAmount] = useState(50_000_000);
  const [excludeST, setExcludeST] = useState(true);
  const [industry, setIndustry] = useState<string | undefined>(undefined);
  const [selected, setSelected] = useState<string | null>(null);

  const query = useQuery({
    queryKey: ["radar", date, topK, minAmount, excludeST, industry],
    queryFn: () =>
      api.radar({
        date: date || undefined,
        topK,
        minAmount,
        excludeST,
        industry,
      }),
    staleTime: 5 * 60 * 1000,
  });

  const detailQuery = useQuery({
    queryKey: ["radar-detail", selected, date],
    queryFn: () => api.radarDetail(selected as string, date || undefined),
    enabled: Boolean(selected),
    staleTime: 5 * 60 * 1000,
  });

  const data = query.data?.data;
  const items = data?.items ?? [];
  const detail = detailQuery.data?.data;

  const industryOptions = useMemo(() => {
    const unique = new Set<string>();
    items.forEach((item) => {
      if (item.industry) unique.add(item.industry);
    });
    return Array.from(unique)
      .sort()
      .map((value) => ({ value, label: value }));
  }, [items]);

  if (query.error) {
    return (
      <Alert
        type="error"
        showIcon
        message="机会雷达加载失败"
        description={
          query.error instanceof Error ? query.error.message : String(query.error)
        }
        action={<Button onClick={() => query.refetch()}>重试</Button>}
      />
    );
  }

  return (
    <div className="page">
      <div className="page-header">
        <div>
          <h2 className="page-title">机会雷达</h2>
          <p className="page-subtitle">
            {data
              ? `交易日 ${data.tradeDate} · 可选 ${data.eligible} 只 · 命中 ${data.total} 只`
              : "加载中"}
            {query.data ? ` · 来源 ${query.data.source}` : ""}
          </p>
        </div>
        <Space>
          {query.data?.stale ? <Tag color="red">数据滞后</Tag> : null}
          <Button onClick={() => query.refetch()} loading={query.isFetching}>
            刷新
          </Button>
        </Space>
      </div>

      {query.data && query.data.warnings.length > 0 ? (
        <Alert
          type="warning"
          showIcon
          message="数据提示"
          description={
            <ul style={{ margin: 0, paddingLeft: 18 }}>
              {query.data.warnings.map((warning) => (
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
            placeholder="交易日 YYYYMMDD"
            style={{ width: 190 }}
            value={dateDraft}
            onChange={(event) => setDateDraft(event.target.value)}
            onSearch={(value) => setDate(value.trim())}
          />
          <Select
            value={topK}
            options={TOPK_OPTIONS}
            style={{ width: 110 }}
            onChange={setTopK}
          />
          <Select
            value={minAmount}
            options={MIN_AMOUNT_OPTIONS}
            style={{ width: 130 }}
            onChange={setMinAmount}
          />
          <Select
            allowClear
            placeholder="全部行业"
            style={{ width: 140 }}
            value={industry}
            options={industryOptions}
            onChange={(value) => setIndustry(value)}
          />
          <Space size={6}>
            <span style={{ color: "#6b7280", fontSize: 13 }}>排除 ST</span>
            <Switch checked={excludeST} onChange={setExcludeST} size="small" />
          </Space>
        </Space>
      </Card>

      <Card size="small" title="候选榜单" extra={<Tag>{items.length} 只</Tag>}>
        <Table<RadarOpportunity>
          className="rank-table"
          rowKey="tsCode"
          size="small"
          loading={query.isFetching}
          dataSource={items}
          columns={columns}
          pagination={{ pageSize: 20, showSizeChanger: false }}
          scroll={{ x: 1000 }}
          onRow={(record) => ({
            onClick: () => setSelected(record.tsCode),
            style: { cursor: "pointer" },
          })}
        />
      </Card>

      <Drawer
        title={detail ? `${detail.name} · ${detail.tsCode}` : "评分分解"}
        width={520}
        open={Boolean(selected)}
        onClose={() => setSelected(null)}
      >
        {detailQuery.error ? (
          <Alert
            type="error"
            showIcon
            message="评分详情加载失败"
            description={
              detailQuery.error instanceof Error
                ? detailQuery.error.message
                : String(detailQuery.error)
            }
          />
        ) : null}

        {detailQuery.isLoading || !detail ? (
          <Spin tip="正在加载评分详情" />
        ) : (
          <Space direction="vertical" size={16} style={{ width: "100%" }}>
            {!detail.eligible ? (
              <Alert
                type="warning"
                showIcon
                message="该标的未通过股票池过滤"
                description={detail.excludeReason ?? "已被过滤，以下评分仅供研究参考"}
              />
            ) : null}

            <Space size={16} wrap>
              <span>
                综合得分{" "}
                <strong style={{ fontSize: 22 }}>{detail.score.toFixed(1)}</strong>
              </span>
              <span>
                置信度 <strong>{detail.confidence.toFixed(1)}</strong>
              </span>
              <Tag color={SIGNAL_COLORS[detail.signal] ?? "default"}>
                {detail.signalLabel}
              </Tag>
              <Tag color={RISK_COLORS[detail.riskLevel] ?? "default"}>
                风险 {RISK_LABELS[detail.riskLevel] ?? detail.riskLevel}
              </Tag>
              <Tag color={detail.dataComplete ? "blue" : "default"}>
                {detail.dataComplete ? "数据完整" : "部分数据缺失"}
              </Tag>
            </Space>

            <Card size="small" title="评分分解">
              <FactorBreakdown detail={detail} />
            </Card>

            <Card size="small" title="入选理由">
              <ul style={{ margin: 0, paddingLeft: 18 }}>
                {detail.keyReasons.map((reason) => (
                  <li key={reason} style={{ marginBottom: 4 }}>
                    {reason}
                  </li>
                ))}
              </ul>
            </Card>

            <Card size="small" title="风险提示">
              {detail.riskFlags.length === 0 ? (
                <span style={{ color: "#6b7280" }}>暂无显著风险标记</span>
              ) : (
                <Space size={6} wrap>
                  {detail.riskFlags.map((flag) => (
                    <Tag color="orange" key={flag}>
                      {flag}
                    </Tag>
                  ))}
                </Space>
              )}
            </Card>

            <Card size="small" title="轻量交易计划">
              <Descriptions
                size="small"
                column={2}
                items={[
                  {
                    key: "entry",
                    label: "入场区间",
                    children: `${detail.tradePlan.entryLow.toFixed(2)} - ${detail.tradePlan.entryHigh.toFixed(2)}`,
                  },
                  {
                    key: "stop",
                    label: "止损",
                    children: detail.tradePlan.stopLoss.toFixed(2),
                  },
                  {
                    key: "target1",
                    label: "目标 1",
                    children: detail.tradePlan.target1.toFixed(2),
                  },
                  {
                    key: "target2",
                    label: "目标 2",
                    children: detail.tradePlan.target2.toFixed(2),
                  },
                  {
                    key: "rr1",
                    label: "盈亏比 1",
                    children: detail.tradePlan.rr1.toFixed(2),
                  },
                  {
                    key: "rr2",
                    label: "盈亏比 2",
                    children: detail.tradePlan.rr2.toFixed(2),
                  },
                  {
                    key: "position",
                    label: "建议仓位",
                    children: `${detail.tradePlan.positionPct.toFixed(1)}%`,
                  },
                ]}
              />
            </Card>
          </Space>
        )}
      </Drawer>
    </div>
  );
}
