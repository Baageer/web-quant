from __future__ import annotations

import math

import pandas as pd
import pytest

from app.backtest.broker import FeeBreakdown
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


def test_panel_marks_stale_when_adj_factor_missing():
    bars = build_bars({"600001.SH": [10.0, 10.0]}, DATES[:2])
    fresh = Panel.from_bars(bars, universe=None, adj_missing_rate=0.0)
    stale = Panel.from_bars(bars, universe=None, adj_missing_rate=0.02)

    assert fresh.stale_data is False
    assert stale.stale_data is True


def test_sells_execute_before_buys_to_free_cash():
    codes = ["600001.SH", "600002.SH", "600003.SH", "600004.SH"]
    bars = build_bars({code: [10.0] * len(DATES) for code in codes}, DATES)
    strategy = ScriptedStrategy(
        {
            DATES[0]: {"600001.SH": 0.45, "600002.SH": 0.45},
            DATES[1]: {"600001.SH": 0.45, "600002.SH": 0.45},
            DATES[2]: {"600003.SH": 0.45, "600004.SH": 0.45},
            DATES[3]: {"600003.SH": 0.45, "600004.SH": 0.45},
        }
    )
    config = make_config(
        max_positions=2, max_position=0.5, initial_cash=1_000_000.0
    )

    output = BacktestEngine(make_panel(bars), strategy, config).run()

    trades = output.trades
    assert (trades["rejection_reason"] != "insufficient_cash").all()
    filled = trades[(trades["status"] == "filled") & (trades["date"] == DATES[3])]
    assert set(filled[filled["action"] == "sell"]["ts_code"]) == {
        "600001.SH",
        "600002.SH",
    }
    assert set(filled[filled["action"] == "buy"]["ts_code"]) == {
        "600003.SH",
        "600004.SH",
    }


def test_buy_notional_scaled_to_available_cash():
    codes = ["600001.SH", "600002.SH", "600003.SH"]
    dates = DATES[:4]
    bars = build_bars({code: [10.0] * len(dates) for code in codes}, dates)
    strategy = ScriptedStrategy(
        {
            dates[0]: {"600001.SH": 0.45, "600002.SH": 0.45},
            dates[1]: {"600001.SH": 0.45, "600002.SH": 0.45},
            dates[2]: {"600001.SH": 0.6, "600002.SH": 0.6},
            dates[3]: {"600001.SH": 0.6, "600002.SH": 0.6},
        }
    )
    config = make_config(
        start_date=dates[0],
        end_date=dates[-1],
        max_positions=2,
        max_position=0.6,
        initial_cash=1_000_000.0,
    )

    output = BacktestEngine(make_panel(bars), strategy, config).run()

    trades = output.trades
    assert (trades["rejection_reason"] != "insufficient_cash").all()
    bought = trades[
        (trades["status"] == "filled")
        & (trades["action"] == "buy")
        & (trades["date"] == dates[3])
        & (trades["ts_code"] == "600001.SH")
    ]
    assert len(bought) == 1
    # 目标加仓 15% 但现金仅剩约 10%，按比例缩量而非整单拒绝
    assert 0 < bought.iloc[0]["shares"] < 15_000
    assert output.nav.iloc[-1]["cash"] < 10_000


def test_stop_loss_sells_next_open_once():
    bars = build_bars({"600001.SH": [10.0, 10.0, 8.5, 8.5, 8.5]}, DATES)
    panel = make_panel(bars)
    strategy = ScriptedStrategy({day: {"600001.SH": 0.5} for day in DATES})
    config = make_config(stop_loss=0.1, rebalance="daily")

    output = BacktestEngine(panel, strategy, config).run()

    stops = output.trades[
        (output.trades["action"] == "sell") & (output.trades["reason"] == "stop_loss")
    ]
    assert len(stops) == 1
    assert stops.iloc[0]["date"] == "20260108"
    held = output.positions[
        (output.positions["date"] == "20260108")
        & (output.positions["ts_code"] == "600001.SH")
    ]
    assert held.empty


def test_take_profit_sells_next_open():
    bars = build_bars({"600001.SH": [10.0, 10.0, 11.5, 11.5, 11.5]}, DATES)
    panel = make_panel(bars)
    strategy = ScriptedStrategy({day: {"600001.SH": 0.5} for day in DATES})
    config = make_config(take_profit=0.1, rebalance="daily")

    output = BacktestEngine(panel, strategy, config).run()

    exits = output.trades[
        (output.trades["action"] == "sell") & (output.trades["reason"] == "take_profit")
    ]
    assert len(exits) == 1
    assert exits.iloc[0]["date"] == "20260108"


def test_risk_controls_disabled_by_default():
    bars = build_bars({"600001.SH": [10.0, 10.0, 8.5, 8.5, 8.5]}, DATES)
    panel = make_panel(bars)
    strategy = ScriptedStrategy({day: {"600001.SH": 0.5} for day in DATES})
    config = make_config(rebalance="daily")

    output = BacktestEngine(panel, strategy, config).run()

    assert not (output.trades["reason"] == "stop_loss").any()
    assert output.positions.iloc[-1]["shares"] > 0


ATR_DATES = [f"202601{day:02d}" for day in range(1, 21)]


def test_atr_trailing_take_profit_exits_after_retrace():
    closes = [10.0, 10.2] * 7 + [10.0] + [12.0, 11.2, 11.2, 11.2, 11.2]
    bars = build_bars({"600001.SH": closes}, ATR_DATES)
    panel = make_panel(bars)
    strategy = ScriptedStrategy({day: {"600001.SH": 0.5} for day in ATR_DATES})
    config = make_config(
        start_date=ATR_DATES[0],
        end_date=ATR_DATES[-1],
        rebalance="daily",
        atr_window=14,
        atr_take_profit=2.0,
    )

    output = BacktestEngine(panel, strategy, config).run()

    exits = output.trades[output.trades["reason"] == "atr_trailing"]
    assert len(exits) == 1
    assert exits.iloc[0]["date"] == ATR_DATES[17]
    assert exits.iloc[0]["action"] == "sell"


def test_atr_take_profit_disabled_by_default():
    closes = [10.0, 10.2] * 7 + [10.0] + [12.0, 11.2, 11.2, 11.2, 11.2]
    bars = build_bars({"600001.SH": closes}, ATR_DATES)
    panel = make_panel(bars)
    strategy = ScriptedStrategy({day: {"600001.SH": 0.5} for day in ATR_DATES})
    config = make_config(
        start_date=ATR_DATES[0], end_date=ATR_DATES[-1], rebalance="daily"
    )

    output = BacktestEngine(panel, strategy, config).run()

    assert not (output.trades["reason"] == "atr_trailing").any()


COOLDOWN_DATES = [
    "20260105",
    "20260106",
    "20260107",
    "20260108",
    "20260109",
    "20260112",
    "20260113",
    "20260114",
    "20260115",
    "20260116",
]


def _cooldown_run(mode: str):
    closes = [10.0, 10.0, 8.5, 8.5, 8.5, 8.5, 9.0, 9.0, 9.0, 9.0]
    bars = build_bars({"600001.SH": closes}, COOLDOWN_DATES)
    panel = make_panel(bars)
    strategy = ScriptedStrategy({day: {"600001.SH": 0.5} for day in COOLDOWN_DATES})
    config = make_config(
        start_date=COOLDOWN_DATES[0],
        end_date=COOLDOWN_DATES[-1],
        rebalance="daily",
        stop_loss=0.1,
        stop_cooldown=mode,
    )
    return BacktestEngine(panel, strategy, config).run()


def _buy_dates(output) -> set[str]:
    buys = output.trades[
        (output.trades["action"] == "buy") & (output.trades["status"] == "filled")
    ]
    return set(buys["date"])


def test_weekly_stop_cooldown_blocks_reentry_within_week():
    output = _cooldown_run("week")

    stops = output.trades[output.trades["reason"] == "stop_loss"]
    assert len(stops) == 1
    buys = _buy_dates(output)
    assert "20260109" not in buys
    assert "20260113" in buys


def test_no_cooldown_allows_reentry_after_stop():
    output = _cooldown_run("none")

    buys = _buy_dates(output)
    assert "20260109" in buys


def test_invalid_risk_control_config_rejected():
    with pytest.raises(ValueError):
        make_config(stop_loss=1.0)
    with pytest.raises(ValueError):
        make_config(take_profit=-0.1)
    with pytest.raises(ValueError):
        make_config(atr_window=1)
    with pytest.raises(ValueError):
        make_config(atr_take_profit=-0.5)
    with pytest.raises(ValueError):
        make_config(stop_cooldown="day")


def test_split_adjusts_shares_and_keeps_equity():
    dates = ["20260105", "20260106"]
    bars = build_bars({"600001.SH": [10.0, 5.0]}, dates)
    bars["adj_factor"] = [1.0, 2.0]
    panel = Panel.from_bars(bars, universe=None, adj_missing_rate=0.0)
    portfolio = Portfolio(100_000)
    portfolio.buy("600001.SH", 1000, 10.0, FeeBreakdown())
    engine = BacktestEngine(panel, ScriptedStrategy({}), make_config())

    engine._adjust_corporate_actions(dates[0], dates[1], portfolio)

    position = portfolio.positions["600001.SH"]
    assert position.shares == 2000
    assert position.available == 2000
    assert portfolio.equity({"600001.SH": 5.0}) == pytest.approx(100_000)


def test_no_split_leaves_shares_unchanged():
    dates = ["20260105", "20260106"]
    bars = build_bars({"600001.SH": [10.0, 10.5]}, dates)
    bars["adj_factor"] = [1.0, 1.0]
    panel = Panel.from_bars(bars, universe=None, adj_missing_rate=0.0)
    portfolio = Portfolio(100_000)
    portfolio.buy("600001.SH", 1000, 10.0, FeeBreakdown())
    engine = BacktestEngine(panel, ScriptedStrategy({}), make_config())

    engine._adjust_corporate_actions(dates[0], dates[1], portfolio)

    assert portfolio.positions["600001.SH"].shares == 1000


def test_weekly_rebalance_only_signals_on_week_end():
    dates = [
        "20260105", "20260106", "20260107", "20260108", "20260109",
        "20260112", "20260113", "20260114", "20260115", "20260116",
    ]
    bars = build_bars({"600001.SH": [10.0] * len(dates)}, dates)
    strategy = ScriptedStrategy({day: {"600001.SH": 0.5} for day in dates})
    config = make_config(
        start_date=dates[0], end_date=dates[-1], rebalance="weekly"
    )

    output = BacktestEngine(make_panel(bars), strategy, config).run()

    filled = output.trades[output.trades["status"] == "filled"]
    assert set(output.trades["signal_date"]) == {"20260109"}
    assert filled.iloc[0]["date"] == "20260112"
    assert filled.iloc[0]["action"] == "buy"


def test_monthly_rebalance_executes_at_next_month_open():
    dates = ["20260129", "20260130", "20260202", "20260203"]
    bars = build_bars({"600001.SH": [10.0] * len(dates)}, dates)
    strategy = ScriptedStrategy({day: {"600001.SH": 0.5} for day in dates})
    config = make_config(
        start_date=dates[0], end_date=dates[-1], rebalance="monthly"
    )

    output = BacktestEngine(make_panel(bars), strategy, config).run()

    filled = output.trades[output.trades["status"] == "filled"]
    assert set(output.trades["signal_date"]) == {"20260130"}
    assert filled.iloc[0]["date"] == "20260202"


def test_unknown_rebalance_mode_is_rejected():
    with pytest.raises(ValueError):
        make_config(rebalance="hourly")


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
