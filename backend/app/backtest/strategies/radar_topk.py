"""机会雷达 TopK 策略（复现线上雷达评分，见 docs/BACKTEST_DESIGN.md §6.2 / §12 P4.1）。

流程与 RadarService 保持一致：
  1. `compute_stock_features` 计算截至 date 的因子特征；
  2. 合并 daily_basic（换手率/量比）、moneyflow（资金流）与沪深300 20 日收益；
  3. 股票池硬过滤（ST、停牌、上市天数、股价、成交额）；
  4. `score_cross_section` 横截面百分位打分，取评分最高的 top_k 等权持有。
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from app.backtest.data_loader import BarContext, Panel
from app.data.universe import is_st_name
from app.services.scoring import RadarConfig, compute_stock_features, score_cross_section

DEFAULT_PARAMS = {
    "top_k": 10,
    "min_score": 50.0,
    "window": 90,
    "min_price": 2.0,
    "min_list_days": 60,
    "min_avg_amount": 50_000_000.0,
    "exclude_st": True,
    "max_position": 0.10,
}


class RadarTopKStrategy:
    name = "radar_topk"
    requires_flow = True

    def __init__(self) -> None:
        self.params: dict = dict(DEFAULT_PARAMS)
        self.config: RadarConfig = RadarConfig()

    def prepare(self, panel: Panel, params: dict) -> None:
        merged = dict(DEFAULT_PARAMS)
        merged.update({key: value for key, value in (params or {}).items() if value is not None})
        self.params = merged
        self.params["top_k"] = max(1, int(merged["top_k"]))
        self.params["min_score"] = float(merged["min_score"])
        self.params["window"] = max(21, int(merged["window"]))
        self.params["min_price"] = float(merged["min_price"])
        self.params["min_list_days"] = max(0, int(merged["min_list_days"]))
        self.params["min_avg_amount"] = float(merged["min_avg_amount"])
        self.params["exclude_st"] = bool(merged["exclude_st"])
        self.params["max_position"] = float(merged["max_position"])
        self.config = RadarConfig(
            min_list_days=self.params["min_list_days"],
            min_price=self.params["min_price"],
        )

    def signal(self, date: str, ctx: BarContext) -> dict[str, float]:
        history = ctx.history_window(int(self.params["window"]))
        if history.empty:
            return {}
        today_codes = set(
            history.loc[history["trade_date"].astype(str) == date, "ts_code"].astype(str)
        )
        if not today_codes:
            return {}

        trading_age = {code: ctx.trading_age(code) for code in ctx.symbols}
        features = compute_stock_features(
            history, stock_basic=ctx.stock_basic(), trading_age=trading_age
        )
        if features.empty:
            return {}

        features = features[features["ts_code"].astype(str).isin(today_codes)]
        if features.empty:
            return {}

        basic = ctx.daily_basic()
        if not basic.empty:
            columns = [
                column
                for column in ("ts_code", "turnover_rate", "volume_ratio")
                if column in basic.columns
            ]
            features = features.merge(
                basic[columns].drop_duplicates("ts_code"), on="ts_code", how="left"
            )
        else:
            features["turnover_rate"] = np.nan
            features["volume_ratio"] = np.nan
        features["volume_ratio"] = features["volume_ratio"].fillna(
            features["volume_ratio_calc"]
        )

        flow = ctx.moneyflow()
        if not flow.empty and {"ts_code", "net_mf_amount"} <= set(flow.columns):
            features = features.merge(
                flow[["ts_code", "net_mf_amount"]].drop_duplicates("ts_code"),
                on="ts_code",
                how="left",
            )
            # net_mf_amount 单位万元，amount 单位千元
            features["capital_ratio"] = np.where(
                features["amount"] > 0,
                features["net_mf_amount"] * 10.0 / features["amount"],
                np.nan,
            )
        else:
            features["net_mf_amount"] = np.nan
            features["capital_ratio"] = np.nan

        index_return = ctx.index_return_20()
        features["rs_20"] = (
            np.nan if index_return is None else features["ret_20"] - index_return
        )

        eligible = self._screen(features, ctx)
        if eligible.empty:
            return {}

        data_complete = (
            not basic.empty
            and not flow.empty
            and index_return is not None
        )
        scored = score_cross_section(
            eligible, config=self.config, data_complete=data_complete
        )
        scored = scored[scored["score"] >= float(self.params["min_score"])]
        if scored.empty:
            return {}

        scored = scored.sort_values(
            ["score", "confidence"], ascending=[False, False]
        ).head(int(self.params["top_k"]))
        weight = min(float(self.params["max_position"]), 1.0 / len(scored))
        return {
            str(row["ts_code"]): weight for _, row in scored.iterrows()
        }

    def _screen(self, features: pd.DataFrame, ctx: BarContext) -> pd.DataFrame:
        """与 RadarService._screen 相同的硬过滤，再叠加面板股票池。"""
        out = features.copy()
        mask = pd.Series(True, index=out.index)
        if self.params["exclude_st"] and "name" in out.columns:
            mask &= ~out["name"].map(is_st_name).fillna(False)
        mask &= (out["vol"].fillna(0) > 0) & (out["amount"].fillna(0) > 0)
        mask &= out["list_days"].fillna(0) >= self.params["min_list_days"]
        mask &= out["close"].fillna(0) >= self.params["min_price"]
        mask &= out["avg_amount_20"].fillna(0) * 1000.0 >= self.params["min_avg_amount"]
        mask &= out["ts_code"].astype(str).map(ctx.is_tradable).fillna(False)
        return out[mask.astype(bool)]
