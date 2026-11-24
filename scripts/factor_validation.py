"""单因子验证：价值 / 动量 / 低波（A 股全市场，月频），支持市值与行业中性化。

因子方向统一为「得分越高越好」：
    value_pb       -ln(PB)，要求 PB > 0（越便宜得分越高）
    value_pe       -ln(PE_TTM)，要求 PE_TTM > 0
    momentum_12_1  过去 12 个月收益，跳过最近 1 个月（用月末复权收盘价）
    lowvol_20      -过去 20 个交易日收益率标准差
    lowvol_60      -过去 60 个交易日收益率标准差
    rev_1m         -过去 1 个月收益（短期反转）
    rev_1w         -过去 5 个交易日收益
    rev_1m_ex1w    -前 5 日收盘 / 前 21 日收盘 - 1，取负（跳过最近 1 周）
    quality_roe            年化 ROE（优先 roe_yearly，缺失时用 roe×4/季度数）
    quality_gross_margin   毛利率
    quality_low_debt       -资产负债率

财务因子按公告日（ann_date）取截至月末最近一期报表，避免前视。

中性化：每月末在截面上把得分对 ln(流通市值) 与行业哑变量做 OLS，取残差作为新得分。
    raw       原始得分
    neutral   对 ln(流通市值) + 行业哑变量回归取残差（--neutralize-mode both）
              --neutralize-mode 可选 both / size / industry

每月末做截面：
    1) 按得分分 5 组等权持有到下月末，统计各组月均收益、Q5-Q1 价差、单调性；
    2) Rank IC（得分与下月收益的截面 Spearman 相关），IR = IC 均值 / IC 标准差；
    3) 分年稳定性。

门槛：|IC 均值| >= 0.03 且 IR >= 0.3。

用法：
    python scripts/factor_validation.py --start-year 2010 --end-year 2025

输出（默认 data/factor_validation/<variant>/）：
    summary.csv        每个因子的总体指标
    ic_monthly.csv     逐月 IC
    group_monthly.csv  逐月分组收益
    yearly.csv         分年 IC 与 Q5-Q1
    correlation.csv    因子间截面 Spearman 相关（月度均值）

注意：行业分类来自当前 stock_basic（静态），存在轻微前视；市值用月末流通市值。
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pyarrow.dataset as ds

ROOT = Path(__file__).resolve().parents[1]
TUSHARE = ROOT / "data" / "raw" / "tushare"
DEFAULT_OUT = ROOT / "data" / "factor_validation"

IC_MIN = 0.03
IR_MIN = 0.3
MIN_LIST_DAYS = 365  # 上市满一年，规避次新股
MIN_STOCKS = 100  # 单月有效样本下限
GROUPS = 5


def _files(dataset: str, start_year: int, end_year: int) -> list[str]:
    root = TUSHARE / dataset
    picked: list[str] = []
    for folder in sorted(p for p in root.iterdir() if p.is_dir()):
        if not folder.name.isdigit():
            continue
        year = int(folder.name)
        if start_year <= year <= end_year:
            picked.extend(str(p) for p in sorted(folder.glob("*.parquet")))
    return picked


def _read(files: list[str], columns: list[str]) -> pd.DataFrame:
    table = ds.dataset(files, format="parquet").to_table(columns=columns)
    frame = table.to_pandas()
    frame["trade_date"] = frame["trade_date"].astype(str)
    return frame


def _pivot(frame: pd.DataFrame, value: str) -> pd.DataFrame:
    return (
        frame.pivot(index="trade_date", columns="ts_code", values=value)
        .sort_index()
        .sort_index(axis=1)
    )


def _fina_frame(start_year: int, end_year: int) -> pd.DataFrame | None:
    """读取财务指标（fina_indicator），按公告日做 point-in-time 对齐。"""
    files = _files("fina_indicator", start_year, end_year)
    if not files:
        return None
    dataset = ds.dataset(files, format="parquet")
    names = set(dataset.schema.names)
    wanted = [
        column
        for column in (
            "ts_code",
            "ann_date",
            "end_date",
            "roe",
            "roe_yearly",
            "grossprofit_margin",
            "debt_to_assets",
        )
        if column in names
    ]
    if not {"ts_code", "ann_date", "end_date"} <= set(wanted):
        return None
    frame = dataset.to_table(columns=wanted).to_pandas()
    frame["ann_date"] = frame["ann_date"].astype(str)
    frame["end_date"] = frame["end_date"].astype(str)
    return frame[frame["ann_date"].str.len() == 8]


def _annualize_roe(reports: pd.DataFrame) -> pd.Series:
    """优先用 roe_yearly；缺失时按报告期季度数把累计 ROE 年化。"""
    if "roe" in reports.columns:
        month = pd.to_datetime(
            reports["end_date"], format="%Y%m%d", errors="coerce"
        ).dt.month
        quarter = ((month - 1) // 3 + 1).clip(lower=1)
        annualized = pd.to_numeric(reports["roe"], errors="coerce") * 4.0 / quarter
    else:
        annualized = pd.Series(np.nan, index=reports.index)
    if "roe_yearly" in reports.columns:
        yearly = pd.to_numeric(reports["roe_yearly"], errors="coerce")
        return yearly.where(yearly.notna(), annualized)
    return annualized


def _quality_scores(
    fina: pd.DataFrame, month_ends: list[str]
) -> dict[str, pd.DataFrame]:
    """按月末取最近一期已公告财报，返回 {因子: 月末 × 股票} 得分矩阵。"""
    reports = fina.sort_values(["ts_code", "ann_date", "end_date"])
    if "roe" in reports.columns or "roe_yearly" in reports.columns:
        reports["_roe_score"] = _annualize_roe(reports)

    definitions: dict[str, tuple[str, float]] = {}
    if "_roe_score" in reports.columns:
        definitions["quality_roe"] = ("_roe_score", 1.0)
    if "grossprofit_margin" in reports.columns:
        definitions["quality_gross_margin"] = ("grossprofit_margin", 1.0)
    if "debt_to_assets" in reports.columns:
        definitions["quality_low_debt"] = ("debt_to_assets", -1.0)
    if not definitions:
        return {}

    collected: dict[str, dict[str, pd.Series]] = {name: {} for name in definitions}
    for date in month_ends:
        snapshot = (
            reports[reports["ann_date"] <= date]
            .drop_duplicates("ts_code", keep="last")
            .set_index("ts_code")
        )
        for name, (column, sign) in definitions.items():
            collected[name][date] = (
                pd.to_numeric(snapshot[column], errors="coerce") * sign
            )
    return {name: pd.DataFrame(rows).T for name, rows in collected.items()}


def _month_ends(dates: list[str]) -> list[str]:
    return [
        date
        for index, date in enumerate(dates)
        if index == len(dates) - 1 or dates[index + 1][:6] != date[:6]
    ]


def _listing_mask(sb: pd.DataFrame, dates: list[str], columns: pd.Index) -> pd.DataFrame:
    """上市满 MIN_LIST_DAYS 的掩码；基础表里查不到的代码按已上市处理。"""
    listed = pd.to_datetime(
        sb.set_index("ts_code")["list_date"], format="%Y%m%d", errors="coerce"
    )
    known = listed.reindex(columns)
    day = pd.to_datetime(pd.Series(dates, index=dates), format="%Y%m%d")
    ages = day.to_numpy()[:, None] - known.to_numpy()[None, :]
    mask = (ages >= np.timedelta64(MIN_LIST_DAYS, "D")) | known.isna().to_numpy()[None, :]
    return pd.DataFrame(mask, index=dates, columns=columns)


def _score_frame(
    score_row: pd.Series, forward_row: pd.Series, mask_row: pd.Series
) -> pd.DataFrame:
    frame = pd.DataFrame({"score": score_row, "fwd": forward_row, "ok": mask_row})
    frame = frame[frame["ok"].fillna(False) & frame["score"].notna() & frame["fwd"].notna()]
    return frame


def _build_designs(
    month_ends: list[str],
    mask: pd.DataFrame,
    log_size: pd.DataFrame,
    industry: pd.Series,
) -> dict[str, dict[str, object]]:
    """预计算每月截面的设计矩阵：截距 + ln(流通市值) + 行业哑变量。"""
    designs: dict[str, dict[str, object]] = {}
    for date in month_ends:
        ok = mask.loc[date].fillna(False)
        codes = ok.index[ok.to_numpy()]
        sizes = log_size.loc[date].reindex(codes)
        keep = sizes.notna().to_numpy()
        codes = codes[keep]
        sizes = sizes.to_numpy(dtype=float)[keep]
        if len(codes) < MIN_STOCKS:
            continue
        labels = industry.reindex(codes).fillna("UNKNOWN")
        dummies = pd.get_dummies(labels, drop_first=True, dtype=float).to_numpy()
        ones = np.ones((len(codes), 1))
        designs[date] = {
            "codes": codes,
            "X_size": np.column_stack([ones, sizes]),
            "X_industry": np.column_stack([ones, dummies]),
            "X_both": np.column_stack([ones, sizes, dummies]),
        }
    return designs


def _residual(
    score_row: pd.Series, design: dict[str, object], mode: str
) -> pd.Series | None:
    codes = design["codes"]
    values = score_row.reindex(codes).to_numpy(dtype=float)
    keep = ~np.isnan(values)
    if int(keep.sum()) < MIN_STOCKS:
        return None
    matrix = design[f"X_{mode}"][keep]
    target = values[keep]
    coef, *_ = np.linalg.lstsq(matrix, target, rcond=None)
    return pd.Series(target - matrix @ coef, index=codes[keep])


def _quantile_returns(
    frame: pd.DataFrame,
) -> tuple[np.ndarray, float, float, dict[int, frozenset]]:
    ranks = frame["score"].rank(method="first")
    bucket = pd.qcut(ranks, GROUPS, labels=False)
    means = frame.groupby(bucket)["fwd"].mean().reindex(range(GROUPS))
    universe = float(frame["fwd"].mean())
    ic = float(frame["score"].corr(frame["fwd"], method="spearman"))
    members = {group: frozenset(frame.index[bucket == group]) for group in range(GROUPS)}
    return means.to_numpy(dtype=float), universe, ic, members


def _annualize(monthly: np.ndarray) -> float:
    if len(monthly) == 0:
        return float("nan")
    return float(np.prod(1.0 + monthly) ** (12.0 / len(monthly)) - 1.0)


def _fmt_pct(value: float) -> str:
    return "n/a" if pd.isna(value) else f"{value * 100:6.2f}%"


def _summarize(
    ic_frame: pd.DataFrame,
    group_frame: pd.DataFrame,
    variants: list[str],
    factors: list[str],
    cost_per_side: float = 0.0,
) -> tuple[list[dict], pd.DataFrame]:
    summary_rows: list[dict] = []
    yearly_rows: list[dict] = []
    for variant in variants:
        for factor_name in factors:
            selector = (ic_frame["variant"] == variant) & (ic_frame["factor"] == factor_name)
            ics = ic_frame[selector]["ic"]
            groups = group_frame[
                (group_frame["variant"] == variant) & (group_frame["factor"] == factor_name)
            ]
            if ics.empty or groups.empty:
                continue
            monthly = {f"q{i}": groups[f"q{i}"].mean() for i in range(1, GROUPS + 1)}
            q_values = np.array([monthly[f"q{i}"] for i in range(1, GROUPS + 1)])
            spread = groups["q5"] - groups["q1"]
            excess = groups["q5"] - groups["universe"]
            turnover_legs = groups["turnover_q1"].fillna(0.0) + groups["turnover_q5"].fillna(0.0)
            spread_net = spread - cost_per_side * turnover_legs
            excess_net = excess - cost_per_side * groups["turnover_q5"].fillna(0.0)
            monotonic = float(
                pd.Series(q_values).corr(pd.Series(np.arange(GROUPS)), method="spearman")
            )
            ic_mean = float(ics.mean())
            ic_std = float(ics.std(ddof=1))
            ic_ir = ic_mean / ic_std if ic_std > 0 else float("nan")
            summary_rows.append(
                {
                    "variant": variant,
                    "factor": factor_name,
                    "months": int(len(ics)),
                    "ic_mean": ic_mean,
                    "ic_std": ic_std,
                    "ic_ir": ic_ir,
                    "ic_t": ic_ir * np.sqrt(len(ics)),
                    "ic_positive_ratio": float((ics > 0).mean()),
                    **monthly,
                    "q5_q1_monthly": float(spread.mean()),
                    "q5_q1_median": float(spread.median()),
                    "q5_q1_win_ratio": float((spread > 0).mean()),
                    "q5_q1_annual": _annualize(spread.to_numpy()),
                    "q5_q1_net_annual": _annualize(spread_net.to_numpy()),
                    "long_excess_annual": float(excess.mean() * 12),
                    "long_excess_net_annual": float(excess_net.mean() * 12),
                    "q5_turnover": float(groups["turnover_q5"].mean()),
                    "monotonicity": monotonic,
                    "avg_stocks": float(groups["stocks"].mean()),
                    "passes": bool(abs(ic_mean) >= IC_MIN and abs(ic_ir) >= IR_MIN),
                }
            )
            yearly_source = ic_frame[selector]
            for year, chunk in yearly_source.groupby("year"):
                year_groups = groups[groups["date"].str[:4] == year]
                yearly_rows.append(
                    {
                        "variant": variant,
                        "factor": factor_name,
                        "year": year,
                        "months": int(len(chunk)),
                        "ic_mean": float(chunk["ic"].mean()),
                        "q5_q1_monthly": float(
                            (year_groups["q5"] - year_groups["q1"]).mean()
                        ),
                        "q5_monthly": float(year_groups["q5"].mean()),
                    }
                )
    return summary_rows, pd.DataFrame(yearly_rows)


def _print_detail(summary_rows: list[dict], variant: str) -> None:
    rows = [row for row in summary_rows if row["variant"] == variant]
    if not rows:
        return
    print()
    print(f"【{variant}】分组收益为月均（等权），价差与多头超额为年化；IC 为 Rank IC。")
    header = (
        f"{'因子':<22}{'月数':>5}{'IC均值':>9}{'IC_IR':>8}{'IC>0':>7}"
        f"{'Q1':>8}{'Q2':>8}{'Q3':>8}{'Q4':>8}{'Q5':>8}"
        f"{'Q5-Q1(年)':>11}{'多头超额(年)':>13}{'单调性':>8}{'通过':>6}"
    )
    print(header)
    print("-" * len(header))
    for row in rows:
        print(
            f"{row['factor']:<22}{row['months']:>5}{row['ic_mean']:>9.4f}{row['ic_ir']:>8.2f}"
            f"{row['ic_positive_ratio'] * 100:>6.1f}%"
            f"{_fmt_pct(row['q1']):>8}{_fmt_pct(row['q2']):>8}{_fmt_pct(row['q3']):>8}"
            f"{_fmt_pct(row['q4']):>8}{_fmt_pct(row['q5']):>8}"
            f"{_fmt_pct(row['q5_q1_annual']):>11}{_fmt_pct(row['long_excess_annual']):>13}"
            f"{row['monotonicity']:>8.2f}{'是' if row['passes'] else '否':>6}"
        )


def _average_correlation(
    monthly_scores: dict[tuple[str, str], dict[str, pd.Series]],
    variant: str,
    factors: list[str],
    month_ends: list[str],
) -> tuple[pd.DataFrame, int] | None:
    """逐月计算因子得分的截面 Spearman 相关，再对月份取均值（成对完整样本）。"""
    labels = list(factors)
    total: pd.DataFrame | None = None
    counts: pd.DataFrame | None = None
    months = 0
    for date in month_ends:
        columns = {
            name: monthly_scores[(variant, name)][date]
            for name in labels
            if date in monthly_scores.get((variant, name), {})
        }
        if len(columns) < 2:
            continue
        corr = pd.DataFrame(columns).corr(method="spearman").reindex(
            index=labels, columns=labels
        )
        valid = corr.notna().astype(float)
        total = corr.fillna(0.0) if total is None else total.add(corr.fillna(0.0))
        counts = valid if counts is None else counts.add(valid)
        months += 1
    if total is None or counts is None or months == 0:
        return None
    return (total / counts.replace(0.0, np.nan)).where(counts > 0), months


SHORT_NAMES = {
    "value_pb": "PB",
    "value_pe": "PE",
    "momentum_12_1": "MOM",
    "lowvol_20": "LV20",
    "lowvol_60": "LV60",
    "rev_1m": "REV1M",
    "rev_1w": "REV1W",
    "rev_1m_ex1w": "REV1MX",
    "quality_roe": "QROE",
    "quality_gross_margin": "QGM",
    "quality_low_debt": "QDEBT",
}


def _print_correlation(matrix: pd.DataFrame, months: int, variant: str) -> None:
    labels = list(matrix.columns)
    print()
    print(f"【{variant} 因子截面相关性】月度 Spearman 均值，共 {months} 期")
    print(f"{'':<9}" + "".join(f"{SHORT_NAMES.get(name, name[:7]):>8}" for name in labels))
    for name in labels:
        row = matrix.loc[name]
        print(
            f"{SHORT_NAMES.get(name, name[:8]):<9}"
            + "".join(f"{row[label]:>8.2f}" for label in labels)
        )


def _print_compare(summary: pd.DataFrame, factors: list[str]) -> None:
    if set(summary["variant"]) != {"raw", "neutral"}:
        return
    print()
    print("【原始 vs 中性化】")
    header = (
        f"{'因子':<22}{'IC原始':>9}{'IC中性':>9}{'IR原始':>8}{'IR中性':>8}"
        f"{'价差原始(年)':>13}{'价差中性(年)':>13}"
        f"{'多头原始(年)':>13}{'多头中性(年)':>13}"
    )
    print(header)
    print("-" * len(header))
    for factor_name in factors:
        raw = summary[(summary["variant"] == "raw") & (summary["factor"] == factor_name)]
        neu = summary[(summary["variant"] == "neutral") & (summary["factor"] == factor_name)]
        if raw.empty or neu.empty:
            continue
        raw, neu = raw.iloc[0], neu.iloc[0]
        print(
            f"{factor_name:<22}{raw['ic_mean']:>9.4f}{neu['ic_mean']:>9.4f}"
            f"{raw['ic_ir']:>8.2f}{neu['ic_ir']:>8.2f}"
            f"{_fmt_pct(raw['q5_q1_annual']):>13}{_fmt_pct(neu['q5_q1_annual']):>13}"
            f"{_fmt_pct(raw['long_excess_annual']):>13}{_fmt_pct(neu['long_excess_annual']):>13}"
        )


def main() -> int:
    parser = argparse.ArgumentParser(description="价值 / 动量 / 低波 单因子验证")
    parser.add_argument("--start-year", type=int, default=2010)
    parser.add_argument("--end-year", type=int, default=2025)
    parser.add_argument(
        "--min-amount",
        type=float,
        default=10000.0,
        help="20 日均成交额下限（千元），默认 1000 万元",
    )
    parser.add_argument(
        "--variants",
        default="raw,neutral",
        help="逗号分隔：raw / neutral，默认两者都算",
    )
    parser.add_argument(
        "--neutralize-mode",
        choices=("both", "size", "industry"),
        default="both",
        help="中性化回归自变量，默认 both（ln 流通市值 + 行业哑变量）",
    )
    parser.add_argument(
        "--cost", type=float, default=0.002, help="单边交易成本，默认 0.002（0.2%%）"
    )
    parser.add_argument("--out", default=str(DEFAULT_OUT))
    args = parser.parse_args()

    variants = [item.strip() for item in args.variants.split(",") if item.strip()]
    if not variants or any(item not in ("raw", "neutral") for item in variants):
        print("--variants 只支持 raw / neutral")
        return 2
    start_year = args.start_year
    end_year = args.end_year
    out_dir = Path(args.out)
    need_neutral = "neutral" in variants

    print(f"[1/5] 读取行情与复权因子（{start_year}~{end_year}）…", flush=True)
    bar_files = _files("bars_by_date", start_year, end_year)
    adj_files = _files("adj_factor", start_year, end_year)
    basic_files = _files("daily_basic", start_year, end_year)
    if not bar_files or not adj_files or not basic_files:
        print("缺少本地数据，请先运行 scripts/bootstrap_data.py")
        return 1

    bars = _read(bar_files, ["ts_code", "trade_date", "close", "amount"])
    close = _pivot(bars, "close")
    amount = _pivot(bars, "amount")
    del bars
    dates = list(close.index)
    print(f"      交易日 {len(dates)} 个，标的 {close.shape[1]} 只", flush=True)

    adj = _read(adj_files, ["ts_code", "trade_date", "adj_factor"])
    adj_matrix = _pivot(adj, "adj_factor").reindex(index=close.index, columns=close.columns)
    del adj
    adj_matrix = adj_matrix.bfill().fillna(1.0)
    close_hfq = close * adj_matrix
    del close, adj_matrix
    print("[2/5] 计算月末复权收盘价、成交额与波动率…", flush=True)

    month_ends = _month_ends(dates)
    me_close = close_hfq.loc[month_ends]
    me_amount = amount.rolling(20, min_periods=10).mean().loc[month_ends]
    del amount

    daily_ret = close_hfq.pct_change(fill_method=None)
    vol20 = daily_ret.rolling(20, min_periods=15).std().loc[month_ends]
    vol60 = daily_ret.rolling(60, min_periods=40).std().loc[month_ends]
    del daily_ret

    positions = {date: index for index, date in enumerate(dates)}

    def _lagged_close(lag: int) -> pd.DataFrame:
        rows = {
            date: close_hfq.loc[dates[positions[date] - lag]]
            for date in month_ends
            if positions[date] >= lag
        }
        return pd.DataFrame(rows).T.reindex(index=month_ends, columns=me_close.columns)

    close_5 = _lagged_close(5)
    close_21 = _lagged_close(21)
    del close_hfq

    print("[3/5] 读取估值 / 市值数据并构建因子…", flush=True)
    basic = _read(basic_files, ["ts_code", "trade_date", "pb", "pe_ttm", "circ_mv"])
    pb = _pivot(basic, "pb").reindex(index=month_ends, columns=me_close.columns)
    pe = _pivot(basic, "pe_ttm").reindex(index=month_ends, columns=me_close.columns)
    circ_mv = _pivot(basic, "circ_mv").reindex(index=month_ends, columns=me_close.columns)
    del basic
    log_size = np.log(circ_mv.where(circ_mv > 0))

    forward = me_close.shift(-1) / me_close - 1.0
    momentum = me_close.shift(1) / me_close.shift(12) - 1.0

    factors: dict[str, pd.DataFrame] = {}
    factors["value_pb"] = -np.log(pb.where(pb > 0))
    factors["value_pe"] = -np.log(pe.where(pe > 0))
    factors["momentum_12_1"] = momentum
    factors["lowvol_20"] = -vol20
    factors["lowvol_60"] = -vol60
    factors["rev_1m"] = -(me_close / me_close.shift(1) - 1.0)
    factors["rev_1w"] = -(me_close / close_5 - 1.0)
    factors["rev_1m_ex1w"] = -(close_5 / close_21 - 1.0)
    del pb, pe, circ_mv, close_5, close_21

    print("      读取财务指标并构建质量因子…", flush=True)
    fina = _fina_frame(start_year - 1, end_year)
    if fina is None:
        print("      未找到 fina_indicator 数据，跳过质量因子")
    else:
        quality = _quality_scores(fina, month_ends)
        for name, frame in quality.items():
            factors[name] = frame.reindex(index=month_ends, columns=me_close.columns)
        if quality:
            print(
                f"      质量因子 {', '.join(quality)}，财报记录 {len(fina)} 条",
                flush=True,
            )
        else:
            print("      fina_indicator 缺少质量字段，跳过质量因子")

    sb = pd.read_parquet(TUSHARE / "stock_basic.parquet")
    if "list_status" in sb.columns:
        sb = sb[sb["list_status"].isin(["L", "D", "P"])]
    listing = _listing_mask(sb, month_ends, me_close.columns)
    name_not_st = ~sb.set_index("ts_code")["name"].fillna("").str.upper().str.contains("ST")
    name_ok = (
        name_not_st.reindex(me_close.columns)
        .astype("boolean")
        .fillna(True)
        .astype(bool)
        .to_numpy()
    )
    name_mask = pd.DataFrame(
        np.broadcast_to(name_ok, listing.shape),
        index=listing.index,
        columns=listing.columns,
    )
    listing = listing & name_mask
    tradable = me_close.notna() & (me_close > 0)
    liquid = me_amount.notna() & (me_amount >= args.min_amount)
    mask = listing & tradable & liquid
    if need_neutral:
        mask = mask & log_size.notna()

    industry = sb.set_index("ts_code")["industry"]
    designs = (
        _build_designs(month_ends, mask, log_size, industry) if need_neutral else {}
    )

    print("[4/5] 逐月计算分组收益与 Rank IC…", flush=True)
    ic_rows: list[dict] = []
    group_rows: list[dict] = []
    monthly_scores: dict[tuple[str, str], dict[str, pd.Series]] = {}
    last_members: dict[tuple[str, str], dict[int, frozenset]] = {}
    for variant in variants:
        for factor_name, score in factors.items():
            last_members.pop((variant, factor_name), None)
            for date in month_ends:
                if date not in score.index:
                    continue
                if variant == "neutral":
                    design = designs.get(date)
                    if design is None:
                        continue
                    residuals = _residual(score.loc[date], design, args.neutralize_mode)
                    if residuals is None:
                        continue
                    frame = pd.DataFrame(
                        {
                            "score": residuals,
                            "fwd": forward.loc[date].reindex(residuals.index),
                        }
                    ).dropna()
                    if len(frame) < MIN_STOCKS:
                        continue
                else:
                    frame = _score_frame(score.loc[date], forward.loc[date], mask.loc[date])
                    if len(frame) < MIN_STOCKS:
                        continue
                monthly_scores.setdefault((variant, factor_name), {})[date] = frame["score"]
                means, universe, ic, members = _quantile_returns(frame)
                previous = last_members.get((variant, factor_name))
                turnover: dict[int, float] = {}
                for group in (0, GROUPS - 1):
                    if previous is None:
                        turnover[group] = float("nan")
                    else:
                        before = previous[group]
                        turnover[group] = 1.0 - len(members[group] & before) / max(1, len(before))
                last_members[(variant, factor_name)] = members
                ic_rows.append(
                    {
                        "variant": variant,
                        "date": date,
                        "factor": factor_name,
                        "ic": ic,
                        "stocks": len(frame),
                    }
                )
                row = {
                    "variant": variant,
                    "date": date,
                    "factor": factor_name,
                    "universe": universe,
                    "stocks": len(frame),
                    "turnover_q1": turnover[0],
                    "turnover_q5": turnover[GROUPS - 1],
                }
                for index, value in enumerate(means):
                    row[f"q{index + 1}"] = value
                group_rows.append(row)

    ic_frame = pd.DataFrame(ic_rows)
    group_frame = pd.DataFrame(group_rows)
    if ic_frame.empty:
        print("没有可用样本，请检查数据区间")
        return 1
    ic_frame["year"] = ic_frame["date"].str[:4]

    print("      计算因子截面相关性…", flush=True)
    correlations: dict[str, pd.DataFrame] = {}
    corr_months: dict[str, int] = {}
    for variant in variants:
        result = _average_correlation(monthly_scores, variant, list(factors), month_ends)
        if result is not None:
            correlations[variant], corr_months[variant] = result
    del monthly_scores

    print("[5/5] 汇总…", flush=True)
    summary_rows, yearly = _summarize(
        ic_frame, group_frame, variants, list(factors), cost_per_side=args.cost
    )
    summary = pd.DataFrame(summary_rows)

    for variant in variants:
        folder = out_dir / variant
        folder.mkdir(parents=True, exist_ok=True)
        summary[summary["variant"] == variant].to_csv(
            folder / "summary.csv", index=False, encoding="utf-8-sig"
        )
        ic_frame[ic_frame["variant"] == variant].to_csv(
            folder / "ic_monthly.csv", index=False, encoding="utf-8-sig"
        )
        group_frame[group_frame["variant"] == variant].to_csv(
            folder / "group_monthly.csv", index=False, encoding="utf-8-sig"
        )
        yearly[yearly["variant"] == variant].to_csv(
            folder / "yearly.csv", index=False, encoding="utf-8-sig"
        )
        if variant in correlations:
            correlations[variant].round(4).to_csv(
                folder / "correlation.csv", encoding="utf-8-sig"
            )

    print()
    print(f"区间 {month_ends[0]} ~ {month_ends[-1]}，月末截面 {len(month_ends)} 期")
    if need_neutral:
        mode_label = {
            "both": "ln(流通市值) + 行业哑变量",
            "size": "ln(流通市值)",
            "industry": "行业哑变量",
        }[args.neutralize_mode]
        print(f"中性化方式：{mode_label} 的截面回归残差")
    for variant in variants:
        _print_detail(summary_rows, variant)
    _print_compare(summary, list(factors))
    for variant in variants:
        if variant in correlations:
            _print_correlation(correlations[variant], corr_months[variant], variant)

    print()
    print(f"【成本后（单边 {args.cost * 100:.2f}%）】")
    cost_header = (
        f"{'因子':<22}{'Q5月换手':>10}{'价差毛(年)':>12}{'价差净(年)':>12}{'多头净超额(年)':>15}"
    )
    print(cost_header)
    print("-" * len(cost_header))
    focus_variant = "neutral" if need_neutral else variants[0]
    for row in summary_rows:
        if row["variant"] != focus_variant:
            continue
        print(
            f"{row['factor']:<22}{row['q5_turnover'] * 100:>9.0f}%"
            f"{_fmt_pct(row['q5_q1_annual']):>12}"
            f"{_fmt_pct(row['q5_q1_net_annual']):>12}"
            f"{_fmt_pct(row['long_excess_net_annual']):>15}"
        )

    print()
    print(f"门槛：|IC 均值| >= {IC_MIN:.2f} 且 IR >= {IR_MIN:.1f}")
    print(f"结果已写入 {out_dir}")
    print()
    print("稳定性补充（Q5-Q1，中性化后）：")
    for row in summary_rows:
        if row["variant"] != "neutral":
            continue
        print(
            f"  {row['factor']:<15}IC t={row['ic_t']:+5.2f}  "
            f"价差中位数 {_fmt_pct(row['q5_q1_median'])}/月  "
            f"胜率 {row['q5_q1_win_ratio'] * 100:4.1f}%  "
            f"样本 {row['avg_stocks']:.0f} 只/月"
        )

    print()
    print("分年 Rank IC / Q5-Q1 月均（中性化后）：")
    focus = "neutral" if need_neutral else variants[0]
    focus_yearly = yearly[yearly["variant"] == focus]
    for factor_name in [name for name in factors if name in set(focus_yearly["factor"])]:
        chunk = focus_yearly[focus_yearly["factor"] == factor_name]
        cells = "  ".join(
            f"{row['year']}:{row['ic_mean']:+.3f}/{row['q5_q1_monthly'] * 100:+.1f}%"
            for row in chunk.to_dict("records")
        )
        print(f"  {factor_name:<22}{cells}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
