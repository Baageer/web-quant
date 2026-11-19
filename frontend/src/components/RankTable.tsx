import { Table, Tag } from "antd";
import type { ColumnsType } from "antd/es/table";

import type { RankItem } from "../api/types";

interface Props {
  items: RankItem[];
  loading?: boolean;
  pageSize?: number;
}

function pctClass(value: number): string {
  return value > 0 ? "up" : value < 0 ? "down" : "flat";
}

const columns: ColumnsType<RankItem> = [
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
    render: (value: string | null | undefined) =>
      value ? <Tag bordered={false}>{value}</Tag> : "-",
  },
  {
    title: "最新价",
    dataIndex: "close",
    key: "close",
    align: "right",
    render: (value: number) => value?.toFixed(2) ?? "-",
  },
  {
    title: "涨跌幅",
    dataIndex: "pctChg",
    key: "pctChg",
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
    align: "right",
    render: (value: number) => value?.toFixed(2) ?? "-",
  },
  {
    title: "换手率",
    dataIndex: "turnoverRate",
    key: "turnoverRate",
    align: "right",
    render: (value: number | null | undefined) =>
      value == null ? "-" : `${value.toFixed(2)}%`,
  },
  {
    title: "量比",
    dataIndex: "volumeRatio",
    key: "volumeRatio",
    align: "right",
    render: (value: number | null | undefined) =>
      value == null ? "-" : value.toFixed(2),
  },
];

export default function RankTable({ items, loading = false, pageSize = 10 }: Props) {
  return (
    <Table<RankItem>
      className="rank-table"
      rowKey="tsCode"
      size="small"
      loading={loading}
      dataSource={items}
      columns={columns}
      pagination={items.length > pageSize ? { pageSize, showSizeChanger: false } : false}
    />
  );
}
