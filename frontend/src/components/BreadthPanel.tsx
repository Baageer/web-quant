import { Card, Col, Row, Statistic } from "antd";

import type { AmountInfo, Breadth } from "../api/types";

interface Props {
  breadth: Breadth;
  amount: AmountInfo;
}

export default function BreadthPanel({ breadth, amount }: Props) {
  const ratioText = `${amount.ratio5d > 0 ? "+" : ""}${(amount.ratio5d * 100).toFixed(1)}%`;
  return (
    <Card size="small" title="市场宽度">
      <Row gutter={[16, 12]}>
        <Col span={6}>
          <Statistic
            title="上涨"
            value={breadth.up}
            valueStyle={{ color: "#d4380d", fontSize: 22 }}
          />
        </Col>
        <Col span={6}>
          <Statistic
            title="下跌"
            value={breadth.down}
            valueStyle={{ color: "#0f8a5f", fontSize: 22 }}
          />
        </Col>
        <Col span={6}>
          <Statistic title="平盘" value={breadth.flat} valueStyle={{ fontSize: 22 }} />
        </Col>
        <Col span={6}>
          <Statistic
            title="涨跌中位数"
            value={breadth.medianPct}
            suffix="%"
            precision={2}
            valueStyle={{ fontSize: 22 }}
          />
        </Col>
        <Col span={6}>
          <Statistic
            title="涨停"
            value={breadth.limitUp}
            valueStyle={{ color: "#d4380d", fontSize: 20 }}
          />
        </Col>
        <Col span={6}>
          <Statistic
            title="跌停"
            value={breadth.limitDown}
            valueStyle={{ color: "#0f8a5f", fontSize: 20 }}
          />
        </Col>
        <Col span={6}>
          <Statistic
            title={`成交额（${amount.unit}）`}
            value={amount.total}
            precision={0}
            valueStyle={{ fontSize: 20 }}
          />
        </Col>
        <Col span={6}>
          <Statistic
            title="较 5 日均值"
            value={ratioText}
            valueStyle={{
              fontSize: 20,
              color: amount.ratio5d >= 0 ? "#d4380d" : "#0f8a5f",
            }}
          />
        </Col>
      </Row>
    </Card>
  );
}
