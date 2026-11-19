"""生成离线演示样本。

样本为确定性随机游走数据，只用于功能演示与测试，不代表真实行情。

用法：
    python scripts/build_offline_samples.py
"""

from __future__ import annotations

import shutil
import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from app.core.config import build_settings  # noqa: E402
from app.data.repository import Repository  # noqa: E402

SEED = 20261119
STOCK_COUNT = 80
TRADING_DAYS = 170

INDEX_CODES = {
    "000001.SH": "上证指数",
    "399001.SZ": "深证成指",
    "399006.SZ": "创业板指",
    "000300.SH": "沪深300",
    "000905.SH": "中证500",
    "000852.SH": "中证1000",
}

INDUSTRIES = [
    "银行",
    "证券",
    "半导体",
    "新能源",
    "医药",
    "白酒",
    "军工",
    "计算机",
    "有色金属",
    "房地产",
]


def _stock_universe() -> pd.DataFrame:
    rows = []
    for index in range(STOCK_COUNT):
        if index < 30:
            symbol = f"60{index + 1:04d}"
            market = "主板"
        elif index < 55:
            symbol = f"00{index + 1:04d}"
            market = "主板"
        elif index < 70:
            symbol = f"30{index + 1:04d}"
            market = "创业板"
        else:
            symbol = f"68{index + 1:04d}"
            market = "科创板"
        suffix = "SH" if symbol.startswith("6") else "SZ"
        name = f"样本{INDUSTRIES[index % len(INDUSTRIES)]}{index + 1:02d}"
        if index in (7, 23):
            name = f"ST样本{index + 1:02d}"
        rows.append(
            {
                "ts_code": f"{symbol}.{suffix}",
                "symbol": symbol,
                "name": name,
                "area": "深圳" if suffix == "SZ" else "上海",
                "industry": INDUSTRIES[index % len(INDUSTRIES)],
                "market": market,
                "list_date": "20100101",
                "list_status": "L",
            }
        )
    return pd.DataFrame(rows)


def _trading_days() -> list[str]:
    days = pd.bdate_range(end="2026-11-18", periods=TRADING_DAYS)
    return [day.strftime("%Y%m%d") for day in days]


def _build_daily(
    stocks: pd.DataFrame,
    days: list[str],
) -> tuple[dict[str, pd.DataFrame], dict[str, pd.DataFrame]]:
    rng = np.random.default_rng(SEED + 1)
    market = rng.normal(0.0003, 0.008, size=len(days))
    frames: dict[str, list[dict]] = {day: [] for day in days}
    basic_frames: dict[str, list[dict]] = {day: [] for day in days}

    for _, stock in stocks.iterrows():
        ts_code = stock["ts_code"]
        seed = int("".join(ch for ch in ts_code if ch.isdigit()) or "0") % 100000
        local = np.random.default_rng(SEED + seed)
        price = float(local.uniform(4.0, 60.0))
        total_share = float(local.uniform(5e8, 8e9))
        float_share = total_share * float(local.uniform(0.5, 0.85))
        beta = float(local.uniform(0.6, 1.4))
        base_volume = float(local.uniform(30_000, 400_000))
        is_st = "ST" in str(stock["name"])
        limit = 0.05 if is_st else (0.20 if ts_code[:2] in {"30", "68"} else 0.10)

        volumes: list[float] = []
        for day_index, day in enumerate(days):
            noise = float(local.normal(0, 0.017))
            pct = beta * market[day_index] + noise
            if local.random() < 0.015:
                pct = limit if local.random() < 0.5 else -limit
            pct = float(np.clip(pct, -limit, limit))

            pre_close = price
            close = max(pre_close * (1 + pct), 0.5)
            open_gap = float(np.clip(local.normal(0, 0.004), -limit, limit))
            open_price = float(np.clip(pre_close * (1 + open_gap), 0.5, pre_close * (1 + limit)))
            high = max(open_price, close) * (1 + abs(float(local.normal(0, 0.006))))
            low = min(open_price, close) * (1 - abs(float(local.normal(0, 0.006))))
            high = min(max(high, close, open_price), pre_close * (1 + limit))
            low = max(min(low, close, open_price), pre_close * (1 - limit), 0.5)

            volume = base_volume * (1 + 6 * abs(pct)) * float(local.uniform(0.6, 1.6))
            volumes.append(volume)
            volume_ratio = volume / float(np.mean(volumes[-5:])) if volumes else 1.0
            amount = volume * 100 * close / 1000.0
            turnover = volume * 100 / float_share * 100

            frames[day].append(
                {
                    "ts_code": ts_code,
                    "trade_date": day,
                    "open": round(open_price, 2),
                    "high": round(high, 2),
                    "low": round(low, 2),
                    "close": round(close, 2),
                    "pre_close": round(pre_close, 2),
                    "change": round(close - pre_close, 2),
                    "pct_chg": round((close / pre_close - 1) * 100, 2),
                    "vol": round(volume, 2),
                    "amount": round(amount, 2),
                }
            )
            basic_frames[day].append(
                {
                    "ts_code": ts_code,
                    "trade_date": day,
                    "turnover_rate": round(turnover, 3),
                    "turnover_rate_f": round(turnover * 1.2, 3),
                    "volume_ratio": round(volume_ratio, 3),
                    "pe_ttm": round(float(local.uniform(8, 60)), 2),
                    "pb": round(float(local.uniform(0.8, 8)), 2),
                    "total_share": total_share,
                    "float_share": float_share,
                    "total_mv": round(close * total_share / 1e4, 2),
                    "circ_mv": round(close * float_share / 1e4, 2),
                }
            )
            price = close

    return (
        {day: pd.DataFrame(rows) for day, rows in frames.items()},
        {day: pd.DataFrame(rows) for day, rows in basic_frames.items()},
    )


def _build_indices(days: list[str]) -> dict[str, pd.DataFrame]:
    rng = np.random.default_rng(SEED + 2)
    market = rng.normal(0.0003, 0.008, size=len(days))
    result: dict[str, list[dict]] = {day: [] for day in days}
    bases = {
        "000001.SH": 3200.0,
        "399001.SZ": 10500.0,
        "399006.SZ": 2100.0,
        "000300.SH": 3800.0,
        "000905.SH": 5800.0,
        "000852.SH": 6200.0,
    }
    for code in INDEX_CODES:
        local = np.random.default_rng(SEED + sum(ord(ch) for ch in code))
        price = bases.get(code, 3000.0)
        beta = float(local.uniform(0.8, 1.2))
        for index, day in enumerate(days):
            pct = float(np.clip(beta * market[index] + local.normal(0, 0.004), -0.08, 0.08))
            pre_close = price
            close = pre_close * (1 + pct)
            high = max(pre_close, close) * (1 + abs(float(local.normal(0, 0.003))))
            low = min(pre_close, close) * (1 - abs(float(local.normal(0, 0.003))))
            result[day].append(
                {
                    "ts_code": code,
                    "trade_date": day,
                    "close": round(close, 2),
                    "open": round(pre_close * (1 + float(local.normal(0, 0.002))), 2),
                    "high": round(high, 2),
                    "low": round(low, 2),
                    "pre_close": round(pre_close, 2),
                    "change": round(close - pre_close, 2),
                    "pct_chg": round(pct * 100, 2),
                    "vol": round(float(local.uniform(1e7, 3e8)), 2),
                    "amount": round(float(local.uniform(2e7, 8e8)), 2),
                }
            )
            price = close
    return {day: pd.DataFrame(rows) for day, rows in result.items()}


def _build_moneyflow(daily: pd.DataFrame) -> pd.DataFrame:
    rng = np.random.default_rng(SEED + 3)
    frame = daily[["ts_code", "trade_date", "amount", "pct_chg"]].copy()
    noise = rng.normal(0, 0.25, size=len(frame))
    ratio = np.clip(frame["pct_chg"].to_numpy() / 10.0 * 0.3 + noise, -1.0, 1.0)
    frame["net_mf_amount"] = frame["amount"].to_numpy() / 10.0 * ratio
    frame["net_mf_vol"] = frame["net_mf_amount"] / 10.0
    return frame[["ts_code", "trade_date", "net_mf_amount", "net_mf_vol"]]


def _build_limit_list(daily: pd.DataFrame, stocks: pd.DataFrame) -> pd.DataFrame:
    merged = daily.merge(stocks[["ts_code", "name"]], on="ts_code", how="left")
    thresholds = np.where(
        merged["name"].astype(str).str.contains("ST"),
        5.0,
        np.where(merged["ts_code"].str.startswith(("30", "68")), 20.0, 10.0),
    )
    up = merged["pct_chg"] >= thresholds - 0.2
    down = merged["pct_chg"] <= -thresholds + 0.2
    rows = []
    for _, row in merged[up | down].iterrows():
        rows.append(
            {
                "trade_date": row["trade_date"],
                "ts_code": row["ts_code"],
                "name": row["name"],
                "close": row["close"],
                "pct_chg": row["pct_chg"],
                "amount": row["amount"],
                "limit_type": "U" if row["pct_chg"] > 0 else "D",
                "first_time": "09:35:00",
                "last_time": "14:30:00",
                "open_times": 0,
                "up_stat": "",
                "limit_times": 1,
                "limit": 1,
            }
        )
    return pd.DataFrame(rows)


def main() -> int:
    settings = build_settings()
    repository = Repository(settings.data_dir, settings.db_path, settings.samples_dir)
    sample_root = settings.samples_dir / "tushare"

    if sample_root.exists():
        shutil.rmtree(sample_root)
    sample_root.mkdir(parents=True, exist_ok=True)
    repository.clear_manifest_layer("sample")

    stocks = _stock_universe()
    days = _trading_days()
    daily, daily_basic = _build_daily(stocks, days)
    indices = _build_indices(days)

    repository.write_stock_basic(stocks, layer="sample", origin="sample")
    calendar = pd.DataFrame({"cal_date": days, "is_open": 1})
    repository.write_trade_cal(calendar, layer="sample", origin="sample")

    total_rows = 0
    for day in days:
        frame = daily[day]
        repository.write_dataset("daily", day, frame, layer="sample", origin="sample")
        repository.write_dataset(
            "daily_basic", day, daily_basic[day], layer="sample", origin="sample"
        )
        adj = frame[["ts_code", "trade_date"]].copy()
        adj["adj_factor"] = 1.0
        repository.write_dataset("adj_factor", day, adj, layer="sample", origin="sample")
        repository.write_dataset(
            "index_daily", day, indices[day], layer="sample", origin="sample"
        )
        repository.write_dataset(
            "moneyflow",
            day,
            _build_moneyflow(frame),
            layer="sample",
            origin="sample",
        )
        limits = _build_limit_list(frame, stocks)
        if not limits.empty:
            repository.write_dataset(
                "limit_list", day, limits, layer="sample", origin="sample"
            )
        total_rows += len(frame)

    print(
        f"样本生成完成：{len(stocks)} 只股票 × {len(days)} 个交易日，共 {total_rows} 行"
    )
    print(f"最新交易日：{days[-1]}")
    print(f"样本目录：{sample_root}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
