import type { SectorItem } from "../api/types";

interface Props {
  title: string;
  items: SectorItem[];
  emptyText?: string;
}

export default function SectorPanel({ title, items, emptyText = "暂无数据" }: Props) {
  return (
    <div className="metric-card">
      <div className="metric-label" style={{ marginBottom: 6 }}>
        {title}
      </div>
      {items.length === 0 ? (
        <div className="metric-delta">{emptyText}</div>
      ) : (
        items.map((item) => {
          const className = item.pctChg > 0 ? "up" : item.pctChg < 0 ? "down" : "flat";
          return (
            <div className="sector-row" key={item.name}>
              <div>
                <span className="sector-name">{item.name}</span>
                <span className="sector-meta">{item.count} 只</span>
              </div>
              <div className={`sector-value ${className}`}>
                {item.pctChg > 0 ? "+" : ""}
                {item.pctChg.toFixed(2)}%
              </div>
            </div>
          );
        })
      )}
    </div>
  );
}
