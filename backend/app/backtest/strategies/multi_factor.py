"""多因子选股：价值（EP）+ 低波 + 短期反转，市值 / 行业中性化后合成，可选市场状态开关。

单因子验证结论（scripts/factor_validation.py，2010-2025，中性化后）：
    value_pe   IC 0.043、IR 0.60、Q5-Q1 年化 9.7%
    lowvol_20  IC 0.073、IR 0.72、Q5-Q1 年化 11.2%
    rev_1m     IC 0.061、IR 0.67、Q5-Q1 年化 15.0%（-过去 1 个月收益，短期反转）
默认权重按 IC 比例：0.25 / 0.40 / 0.35。

策略在每个调仓日（由回测配置 rebalance 控制）做截面：
  1. 取当日 daily_basic 的 PE_TTM 与流通市值，及最近 25 个交易日的复权收盘价；
  2. 计算 EP = -ln(PE_TTM)（越大越便宜）、20 日收益波动率（取负）、-过去 21 日收益；
  3. 股票池过滤：可交易、非 ST、上市天数、股价、20 日均成交额、PE/市值/因子有效；
  4. 三个因子分别对 ln(流通市值) 与行业哑变量做 OLS 取残差，标准化后按权重合成；
  5. 取综合得分前 top_k 等权持有。

市场状态开关（默认关闭）：
  market_filter=true 时，沪深 300 收盘价低于 ma_window 日均线则按 risk_off_weight 降仓
  （0 表示清仓，0.5 表示半仓）；ma_slope_window>0 时还要求均线较 N 日前走平或下行才降仓，
  以过滤震荡市的假突破。因子得分按自然月缓存，因此 rebalance=weekly 时只用周度检查状态、
  因子仍是月度更新，避免无谓换手。

阶梯仓位与再入场确认（默认关闭，兼容旧行为）：
  risk_off_deep_weight    跌破均线超过 risk_off_deep_drawdown 时的仓位（浅跌破用
                          risk_off_weight），不配置则与 risk_off_weight 相同，退化为单档降仓；
  reentry_confirm_days>0  降仓后重新加满仓需要连续 N 个交易日站上均线；
  reentry_buffer          再入场确认的均线缓冲，如 0.02 表示需高于均线 2%。
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from app.backtest.data_loader import BarContext, Panel
from app.data.universe import is_st_name

DEFAULT_PARAMS = {
    "top_k": 50,
    "weight_value": 0.25,
    "weight_vol": 0.40,
    "weight_rev": 0.35,
    "vol_window": 20,
    "rev_window": 21,
    "min_price": 2.0,
    "min_list_days": 250,
    "min_avg_amount": 20_000_000.0,
    "exclude_st": True,
    "neutralize": True,
    "max_position": 0.05,
    "market_filter": False,
    "ma_window": 200,
    "ma_slope_window": 0,
    "risk_off_weight": 0.0,
    "risk_off_deep_weight": None,
    "risk_off_deep_drawdown": 0.05,
    "reentry_confirm_days": 0,
    "reentry_buffer": 0.0,
}


def _zscore(series: pd.Series) -> pd.Series:
    std = float(series.std(ddof=0))
    if not np.isfinite(std) or std == 0.0:
        return pd.Series(0.0, index=series.index)
    return (series - float(series.mean())) / std


class MultiFactorStrategy:
    name = "multi_factor"
    requires_flow = True

    def __init__(self) -> None:
        self.params: dict = dict(DEFAULT_PARAMS)
        self._month: str | None = None
        self._targets: dict[str, float] = {}
        self._exposure: float | None = None

    def prepare(self, panel: Panel, params: dict) -> None:
        merged = dict(DEFAULT_PARAMS)
        merged.update(
            {key: value for key, value in (params or {}).items() if value is not None}
        )
        merged["top_k"] = max(1, int(merged["top_k"]))
        merged["weight_value"] = float(merged["weight_value"])
        merged["weight_vol"] = float(merged["weight_vol"])
        merged["weight_rev"] = float(merged["weight_rev"])
        merged["vol_window"] = max(5, int(merged["vol_window"]))
        merged["rev_window"] = max(2, int(merged["rev_window"]))
        merged["min_price"] = float(merged["min_price"])
        merged["min_list_days"] = max(0, int(merged["min_list_days"]))
        merged["min_avg_amount"] = float(merged["min_avg_amount"])
        merged["exclude_st"] = bool(merged["exclude_st"])
        merged["neutralize"] = bool(merged["neutralize"])
        merged["max_position"] = float(merged["max_position"])
        merged["market_filter"] = bool(merged["market_filter"])
        merged["ma_window"] = max(20, int(merged["ma_window"]))
        merged["ma_slope_window"] = max(0, int(merged["ma_slope_window"]))
        merged["risk_off_weight"] = min(
            max(float(merged["risk_off_weight"]), 0.0), 1.0
        )
        deep_weight = merged["risk_off_deep_weight"]
        if deep_weight is None:
            deep_weight = merged["risk_off_weight"]
        merged["risk_off_deep_weight"] = min(max(float(deep_weight), 0.0), 1.0)
        merged["risk_off_deep_drawdown"] = min(
            max(float(merged["risk_off_deep_drawdown"]), 0.0), 0.5
        )
        merged["reentry_confirm_days"] = max(0, int(merged["reentry_confirm_days"]))
        merged["reentry_buffer"] = max(float(merged["reentry_buffer"]), 0.0)
        self.params = merged
        self._month = None
        self._targets = {}
        self._exposure = None

    def signal(self, date: str, ctx: BarContext) -> dict[str, float]:
        month = date[:6]
        if month != self._month:
            self._targets = self._build_targets(date, ctx)
            self._month = month
        targets = self._targets
        if not targets:
            return {}
        if self.params["market_filter"]:
            scale = self._market_exposure(ctx)
            if scale <= 0.0:
                return {}
            if scale < 1.0:
                return {code: weight * scale for code, weight in targets.items()}
        return dict(targets)

    def _build_targets(self, date: str, ctx: BarContext) -> dict[str, float]:
        basic = ctx.daily_basic()
        required = {"ts_code", "pe_ttm", "circ_mv"}
        if basic.empty or not required <= set(basic.columns):
            return {}
        snapshot = basic.drop_duplicates("ts_code").set_index("ts_code")

        history = ctx.history_window(
            max(int(self.params["vol_window"]), int(self.params["rev_window"])) + 5
        )
        if history.empty or "close_hfq" not in history.columns:
            return {}
        today = history[history["trade_date"].astype(str) == date]
        if today.empty:
            return {}

        closes = history.pivot(
            index="trade_date", columns="ts_code", values="close_hfq"
        )
        returns = closes.pct_change(fill_method=None)
        counts = returns.count()
        volatility = returns.std(ddof=1)
        min_obs = max(10, int(self.params["vol_window"] * 0.75))
        volatility = volatility.where(counts >= min_obs)
        rev_window = int(self.params["rev_window"])
        if len(closes) > rev_window:
            # 短期反转：过去 rev_window 个交易日跌得多的，得分更高
            reversal = -(closes.iloc[-1] / closes.iloc[-1 - rev_window] - 1.0)
        else:
            reversal = pd.Series(np.nan, index=closes.columns)
        avg_amount = history.groupby("ts_code")["amount"].mean() * 1000.0

        codes = pd.Index(today["ts_code"].astype(str), name="ts_code")
        frame = pd.DataFrame(index=codes)
        daily = (
            today.assign(ts_code=today["ts_code"].astype(str))
            .drop_duplicates("ts_code")
            .set_index("ts_code")
        )
        frame["close"] = daily["close"].astype(float).reindex(codes)
        frame["amount"] = daily["amount"].astype(float).reindex(codes)
        frame["volatility"] = volatility.reindex(codes)
        frame["reversal"] = reversal.reindex(codes)
        frame["avg_amount"] = avg_amount.reindex(codes)
        frame["pe_ttm"] = snapshot["pe_ttm"].reindex(codes)
        frame["circ_mv"] = snapshot["circ_mv"].reindex(codes)

        stock_basic = ctx.stock_basic()
        if stock_basic is not None and not stock_basic.empty:
            meta = stock_basic.drop_duplicates("ts_code").set_index("ts_code")
            name = meta["name"].reindex(codes) if "name" in meta.columns else None
            industry = (
                meta["industry"].reindex(codes) if "industry" in meta.columns else None
            )
        else:
            name, industry = None, None
        frame["industry"] = (
            industry.fillna("UNKNOWN").astype(str) if industry is not None else "UNKNOWN"
        )

        mask = frame["close"].fillna(0.0) >= self.params["min_price"]
        mask &= frame["amount"].fillna(0.0) > 0
        mask &= frame["avg_amount"].fillna(0.0) >= self.params["min_avg_amount"]
        if self.params["weight_vol"] > 0:
            mask &= frame["volatility"].notna()
        if self.params["weight_rev"] > 0:
            mask &= frame["reversal"].notna()
        if self.params["weight_value"] > 0:
            mask &= frame["pe_ttm"].fillna(0.0) > 0
        mask &= frame["circ_mv"].fillna(0.0) > 0
        mask &= np.array([bool(ctx.is_tradable(code)) for code in frame.index])
        mask &= np.array(
            [
                int(ctx.trading_age(code)) >= self.params["min_list_days"]
                for code in frame.index
            ]
        )
        if self.params["exclude_st"] and name is not None:
            mask &= ~name.map(is_st_name).fillna(False).to_numpy()

        universe = frame[mask.to_numpy()]
        if universe.empty:
            return {}
        value = -np.log(universe["pe_ttm"].astype(float))
        low_vol = -universe["volatility"].astype(float)
        reversal = universe["reversal"].astype(float)
        if self.params["neutralize"]:
            value = self._residual(value, universe)
            low_vol = self._residual(low_vol, universe)
            reversal = self._residual(reversal, universe)
        score = (
            self.params["weight_value"] * _zscore(value)
            + self.params["weight_vol"] * _zscore(low_vol)
            + self.params["weight_rev"] * _zscore(reversal)
        )
        picked = score.sort_values(ascending=False).head(int(self.params["top_k"]))
        if picked.empty:
            return {}
        weight = min(float(self.params["max_position"]), 1.0 / len(picked))
        return {str(code): weight for code in picked.index}

    def _market_exposure(self, ctx: BarContext) -> float:
        """沪深 300 均线状态对应的目标仓位（0~1）。

        风险开启（站上均线，或均线仍上行）时：
            - 当前已满仓则维持满仓；
            - 降仓后重新加满需满足再入场确认（连续 N 日高于均线/缓冲）。
        风险关闭时按两档降仓：轻微跌破用 risk_off_weight，跌破超过
        risk_off_deep_drawdown 用 risk_off_deep_weight。
        """
        closes = ctx.index_closes()
        window = int(self.params["ma_window"])
        slope_window = int(self.params["ma_slope_window"])
        if closes.empty or len(closes) < window:
            return 1.0
        ma = closes.rolling(window).mean()
        if pd.isna(ma.iloc[-1]):
            return 1.0
        close = float(closes.iloc[-1])
        ma_now = float(ma.iloc[-1])
        rising: bool | None = None
        if (
            slope_window > 0
            and len(ma) > slope_window
            and not pd.isna(ma.iloc[-1 - slope_window])
        ):
            rising = ma_now > float(ma.iloc[-1 - slope_window])

        risk_on = close >= ma_now or rising is True
        current = self._exposure
        if risk_on:
            if current is None or current >= 1.0:
                self._exposure = 1.0
                return 1.0
            buffer = float(self.params["reentry_buffer"])
            need = int(self.params["reentry_confirm_days"])
            if need <= 0:
                self._exposure = 1.0
                return 1.0
            flags = (closes >= ma * (1.0 + buffer)).to_numpy()
            streak = 0
            for flag in flags[::-1]:
                if not flag:
                    break
                streak += 1
            if streak >= need:
                self._exposure = 1.0
                return 1.0
            return current

        deep = close < ma_now * (1.0 - float(self.params["risk_off_deep_drawdown"]))
        exposure = (
            float(self.params["risk_off_deep_weight"])
            if deep
            else float(self.params["risk_off_weight"])
        )
        self._exposure = exposure
        return exposure

    @staticmethod
    def _residual(target: pd.Series, universe: pd.DataFrame) -> pd.Series:
        """对 ln(流通市值) 与行业哑变量回归，返回残差。"""
        size = np.log(universe["circ_mv"].astype(float))
        dummies = pd.get_dummies(
            universe["industry"].fillna("UNKNOWN"), drop_first=True, dtype=float
        )
        matrix = np.column_stack(
            [np.ones(len(universe)), size.to_numpy(dtype=float), dummies.to_numpy()]
        )
        values = target.to_numpy(dtype=float)
        coef, *_ = np.linalg.lstsq(matrix, values, rcond=None)
        return pd.Series(values - matrix @ coef, index=target.index)
