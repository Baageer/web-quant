"""从 TuShare Pro 初始化或刷新本地数据。

用法：
    python scripts/bootstrap_data.py --date 20261118
    python scripts/bootstrap_data.py --start 20260101 --end 20261118
    python scripts/bootstrap_data.py --skip-basic --date 20261118

需要 .env 中配置 TS_TOKEN，并使用 auto 或 live 模式。
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from app.core.config import build_settings  # noqa: E402
from app.core.errors import AppError  # noqa: E402
from app.data.calendar import TradeCalendar  # noqa: E402
from app.data.repository import Repository  # noqa: E402
from app.services.refresh_service import RefreshService  # noqa: E402

CAL_START = "20100101"
CAL_END = "20301231"


def main() -> int:
    parser = argparse.ArgumentParser(description="TuShare 数据初始化 / 刷新")
    parser.add_argument("--date", help="单个交易日 YYYYMMDD，默认最新交易日")
    parser.add_argument("--start", help="起始交易日 YYYYMMDD（批量刷新）")
    parser.add_argument("--end", help="结束交易日 YYYYMMDD（批量刷新）")
    parser.add_argument(
        "--datasets",
        nargs="*",
        help="数据集列表：daily daily_basic adj_factor index_daily moneyflow limit_list",
    )
    parser.add_argument(
        "--fina",
        nargs=2,
        type=int,
        metavar=("START_YEAR", "END_YEAR"),
        help="下载财务指标 fina_indicator（含公告日），如 --fina 2009 2025",
    )
    parser.add_argument("--force", action="store_true", help="忽略已有完整数据强制刷新")
    parser.add_argument("--skip-basic", action="store_true", help="跳过 stock_basic / trade_cal")
    args = parser.parse_args()

    settings = build_settings()
    repository = Repository(settings.data_dir, settings.db_path, settings.samples_dir)
    calendar = TradeCalendar(repository)
    refresh = RefreshService(settings, repository, calendar)

    results: list[dict] = []
    try:
        if not args.skip_basic:
            results.append(refresh.refresh_stock_basic())
            results.append(refresh.refresh_trade_cal(CAL_START, CAL_END))

        if args.fina:
            results.extend(
                refresh.refresh_fina_indicator(
                    args.fina[0], args.fina[1], force=args.force
                )
            )

        if args.start or args.end:
            start = args.start or calendar.latest_trade_date()
            end = args.end or calendar.latest_trade_date()
            results.extend(refresh.bootstrap(start, end, args.datasets, force=args.force))
        elif args.date or not args.fina:
            target = args.date or calendar.latest_trade_date()
            results.extend(refresh.refresh_date(target, args.datasets, force=args.force))
    except AppError as exc:
        print(f"[{exc.code}] {exc.message}")
        return 2

    for item in results:
        dataset = str(item.get("dataset", "?"))
        if item.get("period"):
            dataset = f"{dataset}@{item['period']}"
        status = str(item.get("status", "?"))
        rows = item.get("rows", 0)
        extra = item.get("error") or item.get("reason") or ""
        print(f"{dataset:<24} {status:<10} rows={rows} {extra}".rstrip())

    failures = [item for item in results if item.get("status") == "failed"]
    print(f"完成：{len(results) - len(failures)} 成功，{len(failures)} 失败")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
