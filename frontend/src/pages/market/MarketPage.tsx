import { useQuery, useQueryClient } from "@tanstack/react-query";
import {
  Alert,
  Button,
  Card,
  Col,
  Input,
  Row,
  Select,
  Space,
  Spin,
  Tabs,
  Tag,
} from "antd";
import { useState } from "react";

import { api } from "../../api/client";
import BreadthPanel from "../../components/BreadthPanel";
import IndexCard from "../../components/IndexCard";
import RankTable from "../../components/RankTable";
import SectorPanel from "../../components/SectorPanel";

const REGIME_LABELS: Record<string, { text: string; color: string }> = {
  risk_on: { text: "风险偏好回升", color: "red" },
  neutral: { text: "中性", color: "default" },
  risk_off: { text: "风险偏好回落", color: "green" },
};

const SORT_OPTIONS = [
  { value: "pctChg", label: "涨跌幅" },
  { value: "amount", label: "成交额" },
  { value: "turnoverRate", label: "换手率" },
  { value: "volumeRatio", label: "量比" },
];

export default function MarketPage() {
  const queryClient = useQueryClient();
  const [search, setSearch] = useState("");
  const [sort, setSort] = useState("pctChg");
  const [order, setOrder] = useState<"asc" | "desc">("desc");

  const overviewQuery = useQuery({
    queryKey: ["market-overview"],
    queryFn: () => api.overview(),
  });

  const tickersQuery = useQuery({
    queryKey: ["market-tickers", search, sort, order],
    queryFn: () =>
      api.tickers({
        search: search || undefined,
        sort,
        order,
        limit: 100,
      }),
  });

  const refresh = () => {
    queryClient.invalidateQueries({ queryKey: ["market-overview"] });
    queryClient.invalidateQueries({ queryKey: ["market-tickers"] });
    queryClient.invalidateQueries({ queryKey: ["config"] });
  };

  const error = overviewQuery.error ?? tickersQuery.error;
  if (error) {
    return (
      <Alert
        type="error"
        showIcon
        message="行情数据加载失败"
        description={error instanceof Error ? error.message : String(error)}
        action={<Button onClick={refresh}>重试</Button>}
      />
    );
  }

  const envelope = overviewQuery.data;
  const overview = envelope?.data;

  return (
    <div className="page">
      <div className="page-header">
        <div>
          <h2 className="page-title">行情总览</h2>
          <p className="page-subtitle">
            {overview ? `交易日 ${overview.tradeDate}` : "加载中"}
            {envelope ? ` · 来源 ${envelope.source}` : ""}
          </p>
        </div>
        <Space>
          {envelope?.stale ? <Tag color="red">数据滞后</Tag> : null}
          {overview ? (
            <Tag color={REGIME_LABELS[overview.regime.label]?.color ?? "default"}>
              {REGIME_LABELS[overview.regime.label]?.text ?? overview.regime.label}
            </Tag>
          ) : null}
          <Button onClick={refresh} loading={overviewQuery.isFetching}>
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

      {overviewQuery.isLoading || !overview ? (
        <Card>
          <Spin tip="正在加载行情数据" />
        </Card>
      ) : (
        <>
          <Row gutter={[12, 12]}>
            {overview.indices.map((item) => (
              <Col span={8} key={item.code}>
                <IndexCard item={item} />
              </Col>
            ))}
          </Row>

          <BreadthPanel breadth={overview.breadth} amount={overview.amount} />

          <div className="section-grid">
            <SectorPanel title="行业领涨" items={overview.sectorsTop} />
            <SectorPanel title="行业领跌" items={overview.sectorsBottom} />
          </div>

          <Card size="small" title="榜单">
            <Tabs
              items={[
                {
                  key: "gainers",
                  label: "涨幅榜",
                  children: <RankTable items={overview.leaders.gainers} pageSize={10} />,
                },
                {
                  key: "losers",
                  label: "跌幅榜",
                  children: <RankTable items={overview.leaders.losers} pageSize={10} />,
                },
                {
                  key: "turnover",
                  label: "成交额榜",
                  children: <RankTable items={overview.leaders.turnover} pageSize={10} />,
                },
              ]}
            />
          </Card>

          <Card
            size="small"
            title="全市场行情"
            extra={
              <Space>
                <Input.Search
                  allowClear
                  placeholder="代码或名称"
                  style={{ width: 200 }}
                  onSearch={(value) => setSearch(value.trim())}
                />
                <Select
                  value={sort}
                  options={SORT_OPTIONS}
                  style={{ width: 110 }}
                  onChange={setSort}
                />
                <Select
                  value={order}
                  style={{ width: 90 }}
                  options={[
                    { value: "desc", label: "降序" },
                    { value: "asc", label: "升序" },
                  ]}
                  onChange={(value: "asc" | "desc") => setOrder(value)}
                />
              </Space>
            }
          >
            <RankTable
              items={tickersQuery.data?.data.items ?? []}
              loading={tickersQuery.isFetching}
              pageSize={20}
            />
          </Card>
        </>
      )}
    </div>
  );
}
