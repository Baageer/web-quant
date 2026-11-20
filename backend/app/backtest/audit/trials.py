"""试验台账（见 docs/BACKTEST_DESIGN.md §9.1）。

每次回测写入一条 JSONL：run_id / strategy / params_hash / window / metrics / created_at。
幂等键为 `strategy + params_hash + window`，相同参数与区间重复运行不重复记账，
台账条数供 P4.3 的 DSR 使用。
"""

from __future__ import annotations

import json
import threading
from datetime import datetime
from pathlib import Path
from typing import Any

from app.backtest.metrics import json_safe

SUMMARY_KEYS = (
    "total_return",
    "annual_return",
    "sharpe",
    "sortino",
    "max_drawdown",
    "trade_count",
    "excess_return",
)


class TrialLedger:
    def __init__(self, path: Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._keys: set[str] = {entry["key"] for entry in self.entries()}

    @staticmethod
    def make_key(strategy: str, params_hash: str, window: str) -> str:
        return f"{strategy}|{params_hash}|{window}"

    def entries(self) -> list[dict[str, Any]]:
        if not self.path.exists():
            return []
        output: list[dict[str, Any]] = []
        for line in self.path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                record = json.loads(line)
            except json.JSONDecodeError:
                continue
            if record.get("key"):
                output.append(record)
        return output

    def record(
        self,
        *,
        run_id: str,
        strategy: str,
        params_hash: str,
        window: str,
        metrics: dict[str, Any],
        created_at: str | None = None,
    ) -> dict[str, Any] | None:
        """写入一条试验记录；幂等键已存在时返回 None。"""
        key = self.make_key(strategy, params_hash, window)
        with self._lock:
            if key in self._keys:
                return None
            record = json_safe(
                {
                    "key": key,
                    "run_id": run_id,
                    "strategy": strategy,
                    "params_hash": params_hash,
                    "window": window,
                    "metrics": {
                        name: metrics.get(name) for name in SUMMARY_KEYS if name in metrics
                    },
                    "created_at": created_at
                    or datetime.now().astimezone().isoformat(timespec="seconds"),
                }
            )
            with self.path.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(record, ensure_ascii=False) + "\n")
            self._keys.add(key)
        return record

    def count(self) -> int:
        return len(self._keys)
