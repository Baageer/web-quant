import type { IndexQuote } from "../api/types";

interface Props {
  item: IndexQuote;
}

export default function IndexCard({ item }: Props) {
  const className = item.pctChg > 0 ? "up" : item.pctChg < 0 ? "down" : "flat";
  return (
    <div className="metric-card">
      <div className="metric-label">{item.name}</div>
      <div className={`metric-value ${className}`}>{item.close.toFixed(2)}</div>
      <div className={`metric-delta ${className}`}>
        {item.pctChg > 0 ? "+" : ""}
        {item.pctChg.toFixed(2)}% · {item.amount.toFixed(0)} 亿
      </div>
    </div>
  );
}
