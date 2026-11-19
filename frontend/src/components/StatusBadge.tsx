import { Space, Tag } from "antd";

interface Props {
  mode: string;
  latestTradeDate?: string;
  tokenConfigured?: boolean;
  source?: string;
  stale?: boolean;
}

const MODE_COLORS: Record<string, string> = {
  offline: "default",
  auto: "blue",
  live: "green",
};

const MODE_LABELS: Record<string, string> = {
  offline: "离线",
  auto: "自动",
  live: "实时",
};

export default function StatusBadge({
  mode,
  latestTradeDate,
  tokenConfigured,
  source,
  stale,
}: Props) {
  return (
    <Space size={6} wrap>
      <Tag color={MODE_COLORS[mode] ?? "default"}>{MODE_LABELS[mode] ?? mode}</Tag>
      {latestTradeDate ? <Tag>数据日 {latestTradeDate}</Tag> : null}
      {source ? <Tag color={source === "sample" ? "orange" : "geekblue"}>{source}</Tag> : null}
      {stale ? <Tag color="red">数据滞后</Tag> : null}
      {tokenConfigured === false ? <Tag color="orange">未配置 TS_TOKEN</Tag> : null}
    </Space>
  );
}
