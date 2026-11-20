"""Walk-forward 分析脚本（见 docs/BACKTEST_DESIGN.md §9.2）。

用法示例：
    python scripts/run_walk_forward.py --strategy ma_cross \
        --start 20260326 --end 20261118 \
        --train-years 0.25 --valid-months 2 --step-months 2 \
        --param-grid '{"fast":[5,10],"slow":[20,30]}'

未指定起止日期时使用本地日线数据的首末交易日；参数网格未指定时使用策略默认网格。
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from app.core.config import build_settings  # noqa: E402
from app.data.calendar import TradeCalendar  # noqa: E402
from app.data.repository import Repository  # noqa: E402
from app.backtest.audit.walk_forward import run_walk_forward  # noqa: E402
from app.backtest.metrics import json_safe  # noqa: E402

DEFAULT_GRIDS: dict[str, dict[str, list]] = {
    "ma_cross": {"fast": [5, 10], "slow": [20, 30]},
    "signal_rule": {"min_score": [20, 30, 40]},
    "radar_topk": {"top_k": [5, 10], "min_score": [50, 60]},
}


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="回测 walk-forward 分析")
    parser.add_argument("--strategy", default="ma_cross")
    parser.add_argument("--start", default=None, help="YYYYMMDD，默认取本地数据首日")
    parser.add_argument("--end", default=None, help="YYYYMMDD，默认取本地数据末日")
    parser.add_argument("--train-years", type=float, default=0.25)
    parser.add_argument("--valid-months", type=int, default=2)
    parser.add_argument("--step-months", type=int, default=2)
    parser.add_argument("--param-grid", default=None, help="JSON，如 {\"fast\":[5,10]}")
    parser.add_argument("--initial-cash", type=float, default=1_000_000.0)
    parser.add_argument("--max-positions", type=int, default=20)
    parser.add_argument("--max-position", type=float, default=0.10)
    parser.add_argument("--max-industry", type=float, default=0.30)
    return parser.parse_args()


def main() -> int:
    args = _parse_args()
    settings = build_settings()
    repository = Repository(settings.data_dir, settings.db_path, settings.samples_dir)
    calendar = TradeCalendar(repository)

    dates = repository.available_dates("daily")
    if not dates:
        print("本地没有日线数据，请先运行 scripts/bootstrap_data.py")
        return 1
    start = args.start or dates[0]
    end = args.end or dates[-1]

    if args.param_grid:
        grid = json.loads(args.param_grid)
    else:
        grid = DEFAULT_GRIDS.get(args.strategy, {})

    result = run_walk_forward(
        repository=repository,
        calendar=calendar,
        strategy_name=args.strategy,
        param_grid=grid,
        start_date=start,
        end_date=end,
        train_years=args.train_years,
        valid_months=args.valid_months,
        step_months=args.step_months,
        base_config={
            "initial_cash": args.initial_cash,
            "max_positions": args.max_positions,
            "max_position": args.max_position,
            "max_industry": args.max_industry,
        },
    )

    if not result["folds"]:
        print("区间不足，未能切分出完整的训练/验证窗口，请缩短 train-years 或调整日期")
        return 2

    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    output_dir = Path(settings.data_dir) / "backtest" / "walk_forward" / f"wfa_{stamp}"
    output_dir.mkdir(parents=True, exist_ok=True)
    folds_path = output_dir / "folds.json"
    folds_path.write_text(
        json.dumps(json_safe({key: value for key, value in result.items() if key != "oos_nav"}), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    nav_path = output_dir / "oos_nav.parquet"
    result["oos_nav"].to_parquet(nav_path, index=False)

    print(f"策略：{args.strategy}  区间：{start}~{end}  候选参数：{len(result['candidates'])}")
    print(f"{'fold':<5}{'验证区间':<22}{'参数':<32}{'收益':>9}{'最大回撤':>10}{'Sharpe':>9}")
    for fold in result["folds"]:
        valid = fold["valid"]
        metrics = valid["metrics"]
        print(
            f"{fold['fold']:<5}"
            f"{valid['start']}~{valid['end']:<10}"
            f"{json.dumps(valid.get('params', fold['train']['params']), ensure_ascii=False):<32}"
            f"{metrics.get('total_return', 0.0):>9.2%}"
            f"{metrics.get('max_drawdown', 0.0):>10.2%}"
            f"{metrics.get('sharpe', 0.0):>9.2f}"
        )
    print(f"\n样本外净值：{len(result['oos_nav'])} 个交易日 → {nav_path}")
    print(f"分折结果：{folds_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
