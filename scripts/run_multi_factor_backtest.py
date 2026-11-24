"""运行多因子（价值 EP + 低波，市值/行业中性化）回测并打印核心指标。

用法：
    python scripts/run_multi_factor_backtest.py --start 20100104 --end 20251231 --top-k 50
可选：
    --rebalance monthly|weekly   --no-neutralize
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from app.core.config import build_settings  # noqa: E402
from app.data.calendar import TradeCalendar  # noqa: E402
from app.data.repository import Repository  # noqa: E402
from app.models.schemas import BacktestRequest  # noqa: E402
from app.services.backtest_service import BacktestService  # noqa: E402

KEYS = (
    "total_return",
    "annual_return",
    "sharpe",
    "max_drawdown",
    "calmar",
    "turnover_rate",
    "total_fees",
    "trade_count",
    "win_rate",
)


def main() -> int:
    parser = argparse.ArgumentParser(description="多因子策略回测")
    parser.add_argument("--start", default="20100104")
    parser.add_argument("--end", default="20251231")
    parser.add_argument("--top-k", type=int, default=50)
    parser.add_argument("--weight-value", type=float, default=0.25)
    parser.add_argument("--weight-vol", type=float, default=0.40)
    parser.add_argument("--weight-rev", type=float, default=0.35)
    parser.add_argument("--rebalance", default="monthly")
    parser.add_argument("--max-position", type=float, default=0.05)
    parser.add_argument(
        "--min-avg-amount", type=float, default=20_000_000.0, help="20 日均成交额下限（元）"
    )
    parser.add_argument("--no-neutralize", action="store_true")
    parser.add_argument("--market-filter", action="store_true", help="启用沪深300 均线市场状态开关")
    parser.add_argument("--ma-window", type=int, default=200)
    parser.add_argument("--ma-slope-window", type=int, default=0, help="均线趋势确认窗口，0 关闭")
    parser.add_argument(
        "--risk-off-weight", type=float, default=0.0, help="跌破均线时的仓位比例，0 为空仓"
    )
    parser.add_argument(
        "--risk-off-deep-weight", type=float, default=None, help="深度跌破均线时的仓位，缺省同 risk-off-weight"
    )
    parser.add_argument(
        "--risk-off-deep-drawdown", type=float, default=0.05, help="深度跌破的均线偏离幅度，如 0.05 表示低于均线 5%%"
    )
    parser.add_argument(
        "--reentry-confirm-days", type=int, default=0, help="再入场需连续站上均线的交易日数，0 关闭"
    )
    parser.add_argument(
        "--reentry-buffer", type=float, default=0.0, help="再入场确认的均线缓冲，如 0.02 表示高于均线 2%%"
    )
    parser.add_argument("--initial-cash", type=float, default=1_000_000.0)
    args = parser.parse_args()

    settings = build_settings()
    repository = Repository(settings.data_dir, settings.db_path, settings.samples_dir)
    calendar = TradeCalendar(repository)
    service = BacktestService(settings, repository, calendar)

    request = BacktestRequest(
        start_date=args.start,
        end_date=args.end,
        strategy="multi_factor",
        params={
            "top_k": args.top_k,
            "weight_value": args.weight_value,
            "weight_vol": args.weight_vol,
            "weight_rev": args.weight_rev,
            "neutralize": not args.no_neutralize,
            "min_avg_amount": args.min_avg_amount,
            "market_filter": args.market_filter,
            "ma_window": args.ma_window,
            "ma_slope_window": args.ma_slope_window,
            "risk_off_weight": args.risk_off_weight,
            "risk_off_deep_weight": (
                args.risk_off_deep_weight
                if args.risk_off_deep_weight is not None
                else args.risk_off_weight
            ),
            "risk_off_deep_drawdown": args.risk_off_deep_drawdown,
            "reentry_confirm_days": args.reentry_confirm_days,
            "reentry_buffer": args.reentry_buffer,
        },
        initial_cash=args.initial_cash,
        max_positions=args.top_k,
        max_position=args.max_position,
        max_industry=0.30,
        rebalance=args.rebalance,
    )
    envelope = service.run(request)
    run_id = envelope.data.run_id
    detail = service.get_run(run_id).data
    metrics = detail.metrics or {}

    print(f"run_id={run_id}  区间={args.start}~{args.end}  调仓={args.rebalance}")
    print(
        f"top_k={args.top_k}  权重={args.weight_value}/{args.weight_vol}/{args.weight_rev}  "
        f"中性化={'否' if args.no_neutralize else '是'}  "
        f"市场开关={'MA%d 空仓' % args.ma_window if args.market_filter and args.risk_off_weight <= 0 else ('MA%d 半仓%.0f%%' % (args.ma_window, args.risk_off_weight * 100) if args.market_filter else '关')}"
    )
    for key in KEYS:
        value = metrics.get(key)
        if isinstance(value, float):
            print(f"  {key:<14}{value:>12.4f}")
        else:
            print(f"  {key:<14}{value!s:>12}")
    for warning in envelope.warnings:
        print(f"  警告：{warning}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
