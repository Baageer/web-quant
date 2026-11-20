from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from app.backtest.data_loader import Panel
from app.backtest.metrics import compute_metrics
from app.backtest.portfolio import constrain_targets
from app.backtest.strategies import build_strategy
from app.services.scoring import (
    RadarConfig,
    compute_stock_features,
    score_cross_section,
)


# ---------------------------------------------------------------------------
# 组合约束
# ---------------------------------------------------------------------------


def test_constrain_single_position_cap():
    target, notes = constrain_targets(
        {"600001.SH": 0.5},
        current_weights={},
        max_position=0.1,
        max_industry=0.3,
        max_positions=20,
    )
    assert target["600001.SH"] == pytest.approx(0.1)
    assert any("单票上限" in note for note in notes)


def test_constrain_industry_cap():
    target, notes = constrain_targets(
        {"600001.SH": 0.2, "600002.SH": 0.2},
        current_weights={},
        industry_of={"600001.SH": "银行", "600002.SH": "银行"},
        max_position=0.3,
        max_industry=0.25,
        max_positions=20,
    )
    assert sum(target.values()) == pytest.approx(0.25)
    assert target["600001.SH"] == pytest.approx(0.2)
    assert target["600002.SH"] == pytest.approx(0.05)
    assert any("行业上限" in note for note in notes)


def test_constrain_max_positions_keeps_largest_weights():
    target, notes = constrain_targets(
        {"600001.SH": 0.05, "600002.SH": 0.04, "600003.SH": 0.03},
        current_weights={},
        max_position=0.1,
        max_industry=1.0,
        max_positions=2,
    )
    assert set(target) == {"600001.SH", "600002.SH"}
    assert any("最大持仓数" in note for note in notes)


def test_constrain_turnover_scales_adjustment():
    target, notes = constrain_targets(
        {"600002.SH": 0.10},
        current_weights={"600001.SH": 0.10},
        max_position=0.1,
        max_industry=1.0,
        max_positions=20,
        max_turnover=0.05,
    )
    # 目标换手 10% 被压到 5%：新标的建仓 5%，旧标的保留 5%
    assert target["600002.SH"] == pytest.approx(0.05)
    assert "600001.SH" not in target
    assert any("换手上限" in note for note in notes)


# ---------------------------------------------------------------------------
# 策略
# ---------------------------------------------------------------------------


def _bars(closes: dict[str, list[float]], dates: list[str], volumes: dict[str, float] | None = None) -> pd.DataFrame:
    rows: list[dict] = []
    for code, values in closes.items():
        previous: float | None = None
        volume = float((volumes or {}).get(code, 100_000.0))
        for index, day in enumerate(dates):
            close = float(values[index])
            pre = previous if previous is not None else close
            rows.append(
                {
                    "ts_code": code,
                    "trade_date": day,
                    "open": close,
                    "high": close * 1.01,
                    "low": close * 0.99,
                    "close": close,
                    "pre_close": pre,
                    "pct_chg": (close / pre - 1.0) * 100.0 if pre else 0.0,
                    "vol": volume,
                    "amount": volume * close / 1000.0,
                    "adj_factor": 1.0,
                }
            )
            previous = close
    return pd.DataFrame(rows)


def _dates(count: int, start: str = "2025-09-01") -> list[str]:
    return [day.strftime("%Y%m%d") for day in pd.bdate_range(start=start, periods=count)]


def test_signal_rule_equal_weights_on_uptrend():
    dates = _dates(90)
    closes = {
        "600001.SH": [10.0 + 0.05 * index for index in range(len(dates))],
        "600002.SH": [20.0 + 0.10 * index for index in range(len(dates))],
    }
    panel = Panel.from_bars(_bars(closes, dates), universe=None)
    strategy = build_strategy("signal_rule")
    strategy.prepare(panel, {"min_score": 0.0, "max_positions": 2, "max_position": 0.10})

    target = strategy.signal(dates[-1], panel.as_of(dates[-1]))

    assert set(target) == {"600001.SH", "600002.SH"}
    assert all(weight == pytest.approx(0.10) for weight in target.values())


def test_signal_rule_respects_min_score():
    dates = _dates(90)
    closes = {"600001.SH": [10.0] * len(dates)}
    panel = Panel.from_bars(_bars(closes, dates), universe=None)
    strategy = build_strategy("signal_rule")
    strategy.prepare(panel, {"min_score": 30.0})

    assert strategy.signal(dates[-1], panel.as_of(dates[-1])) == {}


def _radar_panel() -> tuple[Panel, str]:
    dates = _dates(40)
    closes = {
        "600001.SH": [10.0 + 0.10 * index for index in range(len(dates))],
        "600002.SH": [20.0 + 0.01 * index for index in range(len(dates))],
        "600003.SH": [30.0 - 0.10 * index for index in range(len(dates))],
    }
    bars = _bars(
        closes,
        dates,
        volumes={"600001.SH": 8_000_000.0, "600002.SH": 7_000_000.0, "600003.SH": 6_000_000.0},
    )
    stock_basic = pd.DataFrame(
        {
            "ts_code": list(closes),
            "name": ["样本一", "样本二", "样本三"],
            "industry": ["银行", "制造", "医药"],
            "list_date": ["20100101"] * 3,
        }
    )
    day = dates[-1]
    basic = pd.DataFrame(
        {
            "ts_code": list(closes),
            "turnover_rate": [5.0, 3.0, 1.0],
            "volume_ratio": [1.5, 1.0, 0.8],
        }
    )
    flow = pd.DataFrame(
        {"ts_code": list(closes), "net_mf_amount": [500.0, 0.0, -500.0]}
    )
    panel = Panel.from_bars(
        bars,
        universe=None,
        daily_basic={day: basic},
        moneyflow={day: flow},
        index_returns={day: 0.01},
        stock_basic=stock_basic,
        trading_age={code: 10_000 for code in closes},
    )
    return panel, day


def _expected_scores(panel: Panel, day: str) -> pd.DataFrame:
    history = [value for value in panel.dates if value <= day][-40:]
    frame = panel.bars[panel.bars["trade_date"].isin(history)]
    features = compute_stock_features(
        frame,
        stock_basic=panel.stock_basic,
        trading_age=panel.trading_age,
    )
    basic = panel.daily_basic[day]
    features = features.merge(basic, on="ts_code", how="left")
    features["volume_ratio"] = features["volume_ratio"].fillna(features["volume_ratio_calc"])
    features = features.merge(panel.moneyflow[day], on="ts_code", how="left")
    features["capital_ratio"] = np.where(
        features["amount"] > 0, features["net_mf_amount"] * 10.0 / features["amount"], np.nan
    )
    features["rs_20"] = features["ret_20"] - panel.index_returns[day]
    return score_cross_section(features, config=RadarConfig(min_list_days=0), data_complete=True)


def test_radar_topk_matches_online_scoring():
    panel, day = _radar_panel()
    strategy = build_strategy("radar_topk")
    strategy.prepare(
        panel,
        {"top_k": 2, "min_score": 0.0, "min_list_days": 0, "max_position": 0.10},
    )

    target = strategy.signal(day, panel.as_of(day))

    expected = _expected_scores(panel, day).sort_values(
        ["score", "confidence"], ascending=[False, False]
    )
    top = list(expected["ts_code"].astype(str).head(2))
    assert list(target) == top
    assert all(weight == pytest.approx(0.10) for weight in target.values())


def test_radar_topk_filters_low_scores():
    panel, day = _radar_panel()
    strategy = build_strategy("radar_topk")
    strategy.prepare(panel, {"min_score": 101.0})

    assert strategy.signal(day, panel.as_of(day)) == {}


# ---------------------------------------------------------------------------
# 分年/分月指标
# ---------------------------------------------------------------------------


def test_metrics_split_by_year_and_month():
    nav = pd.DataFrame(
        {
            "date": ["20251231", "20260101", "20260102", "20260201"],
            "portfolio_value": [100.0, 110.0, 121.0, 110.0],
            "nav": [1.0, 1.1, 1.21, 1.1],
            "cash": [0.0] * 4,
            "cash_ratio": [0.0] * 4,
            "daily_return": [0.0, 0.1, 0.1, 1.1 / 1.21 - 1.0],
            "drawdown": [0.0, 0.0, 0.0, 1.1 / 1.21 - 1.0],
        }
    )
    metrics = compute_metrics(nav, pd.DataFrame(), initial_cash=100.0)

    assert metrics["yearly_metrics"]["2025"]["return"] == pytest.approx(0.0)
    assert metrics["yearly_metrics"]["2025"]["days"] == 1
    assert metrics["yearly_metrics"]["2026"]["return"] == pytest.approx(0.1)
    assert metrics["yearly_metrics"]["2026"]["days"] == 3
    assert metrics["monthly_metrics"]["2026-01"]["return"] == pytest.approx(0.21)
    # 2026-02 只有单日，区间内没有回撤
    assert metrics["monthly_metrics"]["2026-02"]["max_drawdown"] == pytest.approx(0.0)
