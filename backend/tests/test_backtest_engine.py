from __future__ import annotations

import math

import pandas as pd
import pytest

from app.backtest.engine import BacktestEngine, LookaheadError
from app.backtest.data_loader import Panel
from app.backtest.models import (
    BacktestConfig,
    ExecutionConfig,
    FeeConfig,
    Order,
    UniverseConfig,
)
from app.backtest.portfolio import Portfolio
from app.backtest.strategies import build_strategy

DATES = [f"202601{day:02d}" for day in (5, 6, 7, 8, 9)]


def build_bars(
    closes: dict[str, list[float]],
    dates: list[str],
    *,
    opens: dict[str, list[float]] | None = None,
    missing: set[tuple[str, str]] | None = None,
) -> pd.DataFrame:
    rows: list[dict] = []
    for code, values in closes.items():
        previous: float | None = None
        for index, day in enumerate(dates):
            if missing and (code, day) in missing:
                continue
            close = float(values[index])
            open_price = float(opens[code][index]) if opens else close
            rows.append(
                {
                    "ts_code": code,
                    "trade_date": day,
                    "open": open_price,
                    "high": max(open_price, close),
                    "low": min(open_price, close),
                    "close": close,
                    "pre_close": previous if previous is not None else close,
                    "vol": 10_000.0,
                    "amount": 1_000_000.0,
                    "adj_factor": 1.0,
                }
            )
            previous = close
    return pd.DataFrame(rows)


def make_config(**overrides) -> BacktestConfig:
    base = dict(
        start_date=DATES[0],
        end_date=DATES[-1],
        strategy="scripted",
        params={},
        initial_cash=100_000.0,
        max_positions=1,
        max_position=0.5,
        fees=FeeConfig(),
        execution=ExecutionConfig(signal_ttl_days=1, slippage_bps=0.0),
        universe=UniverseConfig(min_list_days=0, min_avg_amount=0.0),
    )
    base.update(overrides)
    return BacktestConfig(**base)


class ScriptedStrategy:
    name = "scripted"

    def __init__(self, signals: dict[str, dict[str, float]]) -> None:
        self.signals = signals

    def prepare(self, panel: Panel, params: dict) -> None:
        pass

    def signal(self, date: str, ctx) -> dict[str, float]:
        # 策略只能看到 <= date 的数据
        for code in ctx.symbols:
            closes = ctx.closes(code)
            if len(closes):
                assert str(closes.index.max()) <= date
        return self.signals.get(date, {})


def make_panel(bars: pd.DataFrame) -> Panel:
    return Panel.from_bars(bars, universe=None)


def test_signal_fills_at_next_open():
    bars = build_bars({"600001.SH": [10.0, 10.0, 10.0, 10.0, 10.0]}, DATES)
    panel = make_panel(bars)
    strategy = ScriptedStrategy({DATES[0]: {"600001.SH": 0.5}})

    output = BacktestEngine(panel, strategy, make_config()).run()

    filled = output.trades[output.trades["status"] == "filled"]
    assert not filled.empty
    first = filled.iloc[0]
    assert first["date"] == DATES[1]
    assert first["signal_date"] == DATES[0]
    assert first["action"] == "buy"
    assert first["shares"] == 5000
    assert first["price"] == pytest.approx(10.0)

    # 所有成交都必须晚于信号日（禁止同 bar 成交）
    assert (filled["date"] > filled["signal_date"]).all()
    assert output.nav.iloc[-1]["portfolio_value"] > 0


def test_limit_up_rejects_buy():
    bars = build_bars(
        {"600001.SH": [10.0, 11.0, 11.0]},
        DATES[:3],
        opens={"600001.SH": [10.0, 11.0, 11.0]},
    )
    panel = make_panel(bars)
    strategy = ScriptedStrategy({DATES[0]: {"600001.SH": 0.5}})

    output = BacktestEngine(panel, strategy, make_config()).run()

    filled = output.trades[output.trades["status"] == "filled"]
    rejected = output.trades[output.trades["status"] == "rejected"]
    assert filled.empty
    assert len(rejected) == 1
    assert rejected.iloc[0]["rejection_reason"] == "limit_up"
    assert rejected.iloc[0]["date"] == DATES[1]


def test_suspended_order_is_deferred_within_ttl():
    dates = DATES[:4]
    bars = build_bars(
        {"600001.SH": [10.0, 10.0, 10.0, 10.0], "600002.SH": [10.0, 10.0, 10.0, 10.0]},
        dates,
        missing={("600001.SH", dates[1])},
    )
    panel = make_panel(bars)
    strategy = ScriptedStrategy({dates[0]: {"600001.SH": 0.5}})
    config = make_config(end_date=dates[-1], execution=ExecutionConfig(signal_ttl_days=2, slippage_bps=0.0))

    output = BacktestEngine(panel, strategy, config).run()

    filled = output.trades[output.trades["status"] == "filled"]
    assert not filled.empty
    assert filled.iloc[0]["action"] == "buy"
    assert filled.iloc[0]["date"] == dates[2]
    assert filled.iloc[0]["signal_date"] == dates[0]


def test_suspended_order_expires_when_ttl_reached():
    dates = DATES[:4]
    bars = build_bars(
        {"600001.SH": [10.0, 10.0, 10.0, 10.0], "600002.SH": [10.0, 10.0, 10.0, 10.0]},
        dates,
        missing={("600001.SH", dates[1])},
    )
    panel = make_panel(bars)
    strategy = ScriptedStrategy({dates[0]: {"600001.SH": 0.5}})

    output = BacktestEngine(panel, strategy, make_config(end_date=dates[-1])).run()

    expired = output.trades[output.trades["status"] == "expired"]
    assert len(expired) == 1
    assert expired.iloc[0]["date"] == dates[1]
    assert expired.iloc[0]["rejection_reason"] == "signal_expired"
    assert (output.trades["status"] == "filled").sum() == 0


def _oscillating_bars(dates: list[str]) -> pd.DataFrame:
    closes = {
        "600001.SH": [10.0 + 0.8 * math.sin(index / 2.0) + index * 0.02 for index in range(len(dates))],
        "600002.SH": [20.0 + 0.6 * math.sin(index / 3.0) - index * 0.01 for index in range(len(dates))],
    }
    return build_bars(closes, dates)


def test_no_lookahead_future_data_does_not_change_history():
    dates = [f"202602{day:02d}" for day in range(2, 32)]
    bars = _oscillating_bars(dates)
    cutoff_index = 18
    cutoff = dates[cutoff_index]

    base_panel = make_panel(bars)
    base_config = make_config(
        start_date=dates[0],
        end_date=dates[-1],
        params={"fast": 3, "slow": 8},
        max_positions=2,
        max_position=0.5,
        universe=UniverseConfig(min_list_days=0, min_avg_amount=0.0),
    )
    base_output = BacktestEngine(
        base_panel, build_strategy("ma_cross"), base_config
    ).run()

    future = bars["trade_date"] > cutoff
    perturbed = bars.copy()
    for column in ("open", "high", "low", "close"):
        perturbed.loc[future, column] = perturbed.loc[future, column] * 1.5
    perturbed_panel = make_panel(perturbed)
    perturbed_output = BacktestEngine(
        perturbed_panel, build_strategy("ma_cross"), base_config
    ).run()

    base_nav = base_output.nav[base_output.nav["date"] <= cutoff].reset_index(drop=True)
    perturbed_nav = perturbed_output.nav[perturbed_output.nav["date"] <= cutoff].reset_index(drop=True)
    pd.testing.assert_frame_equal(base_nav, perturbed_nav)

    base_trades = base_output.trades[base_output.trades["date"] <= cutoff].reset_index(drop=True)
    perturbed_trades = perturbed_output.trades[
        perturbed_output.trades["date"] <= cutoff
    ].reset_index(drop=True)
    pd.testing.assert_frame_equal(base_trades, perturbed_trades)

    assert not base_trades.empty


def test_bar_context_hides_future_rows():
    dates = [f"202603{day:02d}" for day in range(2, 22)]
    panel = make_panel(_oscillating_bars(dates))
    ctx = panel.as_of(dates[10])
    for code in ctx.symbols:
        closes = ctx.closes(code)
        assert str(closes.index.max()) <= dates[10]
        history = ctx.history(code)
        assert history["trade_date"].max() <= dates[10]


def test_lookahead_error_on_same_day_execution():
    panel = make_panel(build_bars({"600001.SH": [10.0, 10.0, 10.0]}, DATES[:3]))
    engine = BacktestEngine(panel, ScriptedStrategy({}), make_config())
    order = Order(
        ts_code="600001.SH",
        signal_date=DATES[0],
        action="target",
        target_weight=0.5,
        eligible_date=DATES[0],
        ttl_days=1,
    )
    with pytest.raises(LookaheadError):
        engine._execute([order], DATES[0], Portfolio(100_000.0), [])
