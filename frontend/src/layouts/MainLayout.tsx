import { Layout, Menu, Space, Typography } from "antd";
import { useQuery } from "@tanstack/react-query";
import { Outlet, useLocation, useNavigate } from "react-router-dom";

import { api } from "../api/client";
import StatusBadge from "../components/StatusBadge";

const NAV_ITEMS = [
  { key: "/market", label: "行情" },
  { key: "/radar", label: "机会雷达" },
  { key: "/kline", label: "K 线与信号" },
  { key: "/backtest", label: "回测" },
];

export default function MainLayout() {
  const location = useLocation();
  const navigate = useNavigate();
  const { data: config } = useQuery({ queryKey: ["config"], queryFn: api.config });

  const selected = NAV_ITEMS.find((item) => location.pathname.startsWith(item.key));

  return (
    <Layout style={{ minHeight: "100vh" }}>
      <Layout.Sider width={208} theme="light" style={{ borderRight: "1px solid #e8ecf2" }}>
        <div style={{ padding: "20px 20px 12px" }}>
          <Typography.Title level={4} style={{ margin: 0 }}>
            web-quant
          </Typography.Title>
          <Typography.Text type="secondary" style={{ fontSize: 12 }}>
            A 股量化研究
          </Typography.Text>
        </div>
        <Menu
          mode="inline"
          selectedKeys={selected ? [selected.key] : []}
          items={NAV_ITEMS}
          onClick={({ key }) => navigate(key)}
          style={{ borderInlineEnd: "none" }}
        />
      </Layout.Sider>
      <Layout>
        <Layout.Header
          style={{
            background: "#fff",
            borderBottom: "1px solid #e8ecf2",
            padding: "0 24px",
            display: "flex",
            alignItems: "center",
            justifyContent: "space-between",
          }}
        >
          <Space size={12}>
            <Typography.Text strong>{selected?.label ?? "行情"}</Typography.Text>
            <Typography.Text type="secondary" style={{ fontSize: 12 }}>
              数据仅供研究，不构成投资建议
            </Typography.Text>
          </Space>
          <StatusBadge
            mode={config?.mode ?? "offline"}
            latestTradeDate={config?.latestTradeDate}
            tokenConfigured={config?.tokenConfigured}
          />
        </Layout.Header>
        <Layout.Content style={{ padding: 20 }}>
          <Outlet />
          <div className="footer-note">
            数据来源：TuShare Pro / 本地离线样本 · 仅供研究，不构成投资建议
          </div>
        </Layout.Content>
      </Layout>
    </Layout>
  );
}
