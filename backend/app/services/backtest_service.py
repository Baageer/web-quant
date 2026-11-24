"""回测应用服务：解析请求 → 装载数据 → 运行引擎 → 落盘 → 返回摘要。"""

from __future__ import annotations

import hashlib
import json
import subprocess
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from pathlib import Path
from typing import Any

import pandas as pd

from app.core.config import Settings
from app.core.errors import InvalidParamError, RunNotFoundError
from app.core.logging import get_logger
from app.data.calendar import TradeCalendar
from app.data.repository import Repository
from app.models.schemas import (
    BacktestAuditData,
    BacktestNavData,
    BacktestRequest,
    BacktestRunDetail,
    BacktestRunList,
    BacktestRunStatus,
    BacktestRunSummary,
    BacktestTradeData,
    Envelope,
)
from app.backtest.data_loader import load_panel
from app.backtest.engine import BacktestEngine
from app.backtest.audit.runner import config_from_manifest, run_parameter_audit
from app.backtest.audit.trials import TrialLedger
from app.backtest.metrics import compute_metrics, json_safe
from app.backtest.models import (
    COOLDOWN_MODES,
    REBALANCE_MODES,
    BacktestConfig,
    BacktestResult,
)
from app.backtest.result_store import ResultStore
from app.backtest.strategies import build_strategy

logger = get_logger(__name__)

ENGINE_VERSION = "P4.4"


class BacktestService:
    def __init__(
        self,
        settings: Settings,
        repository: Repository,
        calendar: TradeCalendar,
        store: ResultStore | None = None,
    ) -> None:
        self._settings = settings
        self._repository = repository
        self._calendar = calendar
        self.store = store or ResultStore(settings.data_dir / "backtest")
        self.trials = TrialLedger(self.store.root / "trials.jsonl")
        self._executor = ThreadPoolExecutor(
            max_workers=1, thread_name_prefix="backtest"
        )

    # ---- 运行 ----

    def submit(self, request: BacktestRequest) -> Envelope[BacktestRunSummary]:
        """提交异步任务：同步校验参数，返回 queued 状态，后台线程执行。"""
        config = self.build_config(request)
        build_strategy(config.strategy)  # 未知策略在提交阶段直接报 400
        run_id = self.store.new_run_id()
        manifest = self._base_manifest(run_id, config)
        self.store.write_manifest(run_id, manifest)
        self._executor.submit(self._run_job, run_id, config)
        return self._envelope(manifest)

    def run(self, request: BacktestRequest) -> Envelope[BacktestRunSummary]:
        """同步执行（脚本、测试与内部调用）。"""
        config = self.build_config(request)
        build_strategy(config.strategy)
        run_id = self.store.new_run_id()
        manifest = self._base_manifest(run_id, config)
        self.store.write_manifest(run_id, manifest)
        try:
            manifest = self._execute(run_id, config)
        except Exception as exc:
            self._mark_failed(run_id, exc)
            raise
        return self._envelope(manifest)

    def _run_job(self, run_id: str, config: BacktestConfig) -> None:
        try:
            self._execute(run_id, config)
        except Exception as exc:  # 后台任务不能抛出，落盘 failed 状态
            logger.exception("回测任务失败：%s", run_id)
            self._mark_failed(run_id, exc)

    def _execute(self, run_id: str, config: BacktestConfig) -> dict[str, Any]:
        manifest = self.store.load_manifest(run_id) or self._base_manifest(
            run_id, config
        )
        manifest["status"] = "running"
        manifest["started_at"] = _now()
        self.store.write_manifest(run_id, manifest)

        strategy = build_strategy(config.strategy)
        panel = load_panel(
            self._repository,
            self._calendar,
            config,
            need_flow=strategy.requires_flow,
        )
        engine = BacktestEngine(panel, strategy, config)
        output = engine.run()
        metrics = compute_metrics(
            output.nav,
            output.trades,
            initial_cash=config.initial_cash,
            benchmark=panel.benchmark,
            data_quality={
                "suspension_deferrals": output.deferred_count,
                "adj_factor_missing_rate": panel.adj_missing_rate,
            },
        )
        manifest = self._complete_manifest(manifest, panel, metrics, output)
        result = BacktestResult(
            run_id=run_id,
            config=config,
            nav=output.nav,
            trades=output.trades,
            positions=output.positions,
            metrics=metrics,
            manifest=manifest,
            warnings=output.warnings,
        )
        self.store.save(result)

        trial = self.trials.record(
            run_id=run_id,
            strategy=config.strategy,
            params_hash=str(manifest["strategy_fingerprint"]),
            window=f"{config.start_date}~{config.end_date}",
            metrics=metrics,
            created_at=str(manifest["created_at"]),
        )
        if trial is None:
            manifest["warnings"] = list(manifest.get("warnings") or []) + [
                "相同参数与区间的试验已存在，本次未重复记账"
            ]
            self.store.write_manifest(run_id, manifest)
        return manifest

    def _mark_failed(self, run_id: str, exc: Exception) -> None:
        manifest = self.store.load_manifest(run_id) or {}
        manifest.update(
            {
                "status": "failed",
                "finished_at": _now(),
                "error": _error_message(exc),
            }
        )
        self.store.write_manifest(run_id, json_safe(manifest))

    # ---- 查询 ----

    def get_run(self, run_id: str) -> Envelope[BacktestRunDetail]:
        manifest = self.store.load_manifest(run_id)
        if manifest is None:
            raise RunNotFoundError(f"回测记录不存在：{run_id}", detail={"run_id": run_id})
        metrics = self.store.load_metrics(run_id) or {}
        return Envelope(
            ok=True,
            source="backtest",
            as_of=str(manifest.get("created_at") or _now()),
            stale=bool(manifest.get("stale_data")),
            warnings=list(manifest.get("warnings") or []),
            data=BacktestRunDetail(run_id=run_id, manifest=manifest, metrics=metrics),
        )

    def get_status(self, run_id: str) -> Envelope[BacktestRunStatus]:
        manifest = self.store.load_manifest(run_id)
        if manifest is None:
            raise RunNotFoundError(f"回测记录不存在：{run_id}", detail={"run_id": run_id})
        return Envelope(
            ok=True,
            source="backtest",
            as_of=str(manifest.get("created_at") or _now()),
            stale=bool(manifest.get("stale_data")),
            warnings=[],
            data=BacktestRunStatus(
                run_id=run_id,
                status=str(manifest.get("status") or "queued"),
                error=manifest.get("error"),
                created_at=manifest.get("created_at"),
                started_at=manifest.get("started_at"),
                finished_at=manifest.get("finished_at"),
            ),
        )

    def audit_run(
        self,
        run_id: str,
        *,
        param_grid: dict[str, list[Any]] | None = None,
        thresholds: dict[str, float] | None = None,
        n_blocks: int = 8,
        n_test_blocks: int = 2,
        purge_blocks: int = 1,
    ) -> Envelope[BacktestAuditData]:
        manifest = self.store.load_manifest(run_id)
        if manifest is None:
            raise RunNotFoundError(f"回测记录不存在：{run_id}", detail={"run_id": run_id})
        if manifest.get("status") != "completed":
            raise InvalidParamError("回测尚未完成，不能执行审计", detail={"run_id": run_id})

        audit = run_parameter_audit(
            repository=self._repository,
            calendar=self._calendar,
            config=config_from_manifest(manifest),
            param_grid=param_grid,
            thresholds=thresholds,
            n_blocks=n_blocks,
            n_test_blocks=n_test_blocks,
            purge_blocks=purge_blocks,
            extra_trials=max(self.trials.count() - 1, 0),
        )
        self.store.write_audit(run_id, audit)
        manifest["verdict"] = audit["verdict"]
        manifest["audited_at"] = audit["created_at"]
        self.store.write_manifest(run_id, json_safe(manifest))
        return Envelope(
            ok=True,
            source="backtest",
            as_of=str(audit["created_at"]),
            stale=bool(manifest.get("stale_data")),
            warnings=list(audit.get("reasons") or []),
            data=BacktestAuditData(run_id=run_id, audit=audit),
        )

    def get_audit(self, run_id: str) -> Envelope[BacktestAuditData]:
        manifest = self.store.load_manifest(run_id)
        if manifest is None:
            raise RunNotFoundError(f"回测记录不存在：{run_id}", detail={"run_id": run_id})
        audit = self.store.load_audit(run_id)
        if audit is None:
            raise InvalidParamError("该回测尚未执行审计", detail={"run_id": run_id})
        return Envelope(
            ok=True,
            source="backtest",
            as_of=str(audit.get("created_at") or _now()),
            stale=bool(manifest.get("stale_data")),
            warnings=list(audit.get("reasons") or []),
            data=BacktestAuditData(run_id=run_id, audit=audit),
        )

    def list_runs(self, *, limit: int = 20, offset: int = 0) -> Envelope[BacktestRunList]:
        items, total = self.store.list_runs(limit=limit, offset=offset)
        payload = [
            {
                "runId": item.get("run_id"),
                "strategy": item.get("strategy"),
                "startDate": item.get("start_date"),
                "endDate": item.get("end_date"),
                "createdAt": item.get("created_at"),
                "status": item.get("status", "completed"),
                "totalReturn": (item.get("metrics_summary") or {}).get("total_return"),
                "tradeCount": (item.get("metrics_summary") or {}).get("trade_count"),
            }
            for item in items
        ]
        return Envelope(
            ok=True,
            source="backtest",
            as_of=datetime.now().astimezone().isoformat(timespec="seconds"),
            stale=False,
            warnings=[],
            data=BacktestRunList(total=total, items=payload),
        )

    def get_nav(self, run_id: str) -> Envelope[BacktestNavData]:
        manifest = self._manifest(run_id)
        frame = self.store.load_nav(run_id)
        return Envelope(
            ok=True,
            source="backtest",
            as_of=self._as_of(manifest),
            stale=bool(manifest.get("stale_data")),
            warnings=[],
            data=BacktestNavData(run_id=run_id, items=_records(frame)),
        )

    def get_trades(self, run_id: str) -> Envelope[BacktestTradeData]:
        manifest = self._manifest(run_id)
        frame = self.store.load_trades(run_id)
        return Envelope(
            ok=True,
            source="backtest",
            as_of=self._as_of(manifest),
            stale=bool(manifest.get("stale_data")),
            warnings=[],
            data=BacktestTradeData(run_id=run_id, items=_records(frame)),
        )

    # ---- 配置 ----

    def build_config(self, request: BacktestRequest) -> BacktestConfig:
        start = _normalize_date(request.start_date, "startDate")
        end = _normalize_date(request.end_date, "endDate")
        if start > end:
            raise InvalidParamError("startDate 不能晚于 endDate")
        if request.initial_cash <= 0:
            raise InvalidParamError("initialCash 必须大于 0")
        if not 0 < request.max_position <= 1:
            raise InvalidParamError("maxPosition 必须在 (0, 1] 区间")
        if request.max_positions < 1:
            raise InvalidParamError("maxPositions 必须大于等于 1")
        if not 0 < request.max_industry <= 1:
            raise InvalidParamError("maxIndustry 必须在 (0, 1] 区间")
        if request.max_turnover is not None and not 0 < request.max_turnover <= 1:
            raise InvalidParamError("maxTurnover 必须在 (0, 1] 区间")
        if not 0 <= request.stop_loss < 1:
            raise InvalidParamError("stopLoss 必须在 [0, 1) 区间，0 表示关闭")
        if request.take_profit < 0:
            raise InvalidParamError("takeProfit 必须大于等于 0，0 表示关闭")
        if request.atr_window < 2:
            raise InvalidParamError("atrWindow 必须大于等于 2")
        if request.atr_take_profit < 0:
            raise InvalidParamError("atrTakeProfit 必须大于等于 0，0 表示关闭")
        if str(request.stop_cooldown).strip().lower() not in COOLDOWN_MODES:
            raise InvalidParamError(
                f"stopCooldown 必须是 {', '.join(COOLDOWN_MODES)} 之一"
            )
        rebalance = str(request.rebalance or "daily").strip().lower()
        if rebalance not in REBALANCE_MODES:
            raise InvalidParamError(
                f"rebalance 必须是 {' / '.join(REBALANCE_MODES)}",
                detail={"rebalance": request.rebalance},
            )

        symbols = None
        if request.symbols:
            cleaned = tuple(
                code.strip().upper() for code in request.symbols if code and code.strip()
            )
            if not cleaned:
                raise InvalidParamError("symbols 不能为空")
            symbols = cleaned

        return BacktestConfig(
            start_date=start,
            end_date=end,
            strategy=request.strategy or "ma_cross",
            params=dict(request.params or {}),
            initial_cash=float(request.initial_cash),
            symbols=symbols,
            max_positions=int(request.max_positions),
            max_position=float(request.max_position),
            max_industry=float(request.max_industry),
            max_turnover=(
                float(request.max_turnover)
                if request.max_turnover is not None
                else None
            ),
            rebalance=rebalance,
            stop_loss=float(request.stop_loss),
            take_profit=float(request.take_profit),
            atr_window=int(request.atr_window),
            atr_take_profit=float(request.atr_take_profit),
            stop_cooldown=str(request.stop_cooldown).strip().lower(),
        )

    # ---- 产物 ----

    def _base_manifest(
        self, run_id: str, config: BacktestConfig
    ) -> dict[str, Any]:
        fingerprint = hashlib.sha1(
            json.dumps(
                {"strategy": config.strategy, "params": config.params},
                sort_keys=True,
                ensure_ascii=False,
            ).encode("utf-8")
        ).hexdigest()[:16]
        return json_safe(
            {
                "run_id": run_id,
                "created_at": _now(),
                "started_at": None,
                "finished_at": None,
                "status": "queued",
                "error": None,
                "engine_version": ENGINE_VERSION,
                "strategy": config.strategy,
                "strategy_fingerprint": fingerprint,
                "params": config.params,
                "start_date": config.start_date,
                "end_date": config.end_date,
                "actual_start": None,
                "actual_end": None,
                "trading_days": 0,
                "symbols_total": 0,
                "initial_cash": config.initial_cash,
                "final_value": None,
                "data_origin": None,
                "data_latest": self._repository.latest_date("daily"),
                "mode": self._settings.mode.value,
                "stale_data": False,
                "git_commit": _git_commit(self._settings.root_dir),
                "config": config.as_dict(),
                "warnings": [],
                "metrics_summary": {"total_return": 0.0, "trade_count": 0},
                "data_points": {"nav": 0, "trades": 0, "positions": 0},
            }
        )

    def _complete_manifest(
        self,
        manifest: dict[str, Any],
        panel,
        metrics: dict[str, Any],
        output,
    ) -> dict[str, Any]:
        manifest.update(
            {
                "status": "completed",
                "finished_at": _now(),
                "error": None,
                "actual_start": panel.dates[0],
                "actual_end": panel.dates[-1],
                "trading_days": len(panel.dates),
                "symbols_total": len(panel.symbols),
                "final_value": metrics.get("final_value"),
                "data_origin": panel.origin,
                "data_latest": self._repository.latest_date("daily"),
                "stale_data": bool(panel.stale_data),
                "warnings": list(output.warnings),
                "metrics_summary": {
                    "total_return": metrics.get("total_return"),
                    "trade_count": metrics.get("trade_count"),
                },
                "data_points": {
                    "nav": int(len(output.nav)),
                    "trades": int(len(output.trades)),
                    "positions": int(len(output.positions)),
                },
            }
        )
        return json_safe(manifest)

    def _envelope(self, manifest: dict[str, Any]) -> Envelope[BacktestRunSummary]:
        return Envelope(
            ok=True,
            source="backtest",
            as_of=str(manifest.get("created_at") or _now()),
            stale=bool(manifest.get("stale_data")),
            warnings=list(manifest.get("warnings") or []),
            data=self._summary(manifest),
        )

    def _summary(self, manifest: dict[str, Any]) -> BacktestRunSummary:
        return BacktestRunSummary(
            run_id=str(manifest["run_id"]),
            status=str(manifest.get("status", "completed")),
            strategy=str(manifest.get("strategy", "")),
            start_date=str(manifest.get("start_date", "")),
            end_date=str(manifest.get("end_date", "")),
            actual_start=manifest.get("actual_start"),
            actual_end=manifest.get("actual_end"),
            initial_cash=float(manifest.get("initial_cash") or 0.0),
            final_value=float(manifest.get("final_value") or 0.0),
            total_return=float(
                (manifest.get("metrics_summary") or {}).get("total_return") or 0.0
            ),
            trade_count=int(
                (manifest.get("metrics_summary") or {}).get("trade_count") or 0
            ),
            warnings=list(manifest.get("warnings") or []),
        )

    def _manifest(self, run_id: str) -> dict[str, Any]:
        manifest = self.store.load_manifest(run_id)
        if manifest is None:
            raise RunNotFoundError(f"回测记录不存在：{run_id}", detail={"run_id": run_id})
        return manifest

    @staticmethod
    def _as_of(manifest: dict[str, Any]) -> str:
        return str(
            manifest.get("created_at")
            or datetime.now().astimezone().isoformat(timespec="seconds")
        )


def _now() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def _error_message(exc: Exception) -> str:
    message = getattr(exc, "message", None)
    return str(message or exc or exc.__class__.__name__)


def _normalize_date(value: str, field: str) -> str:
    text = str(value or "").strip().replace("-", "")
    if len(text) != 8 or not text.isdigit():
        raise InvalidParamError(f"{field} 必须是 YYYYMMDD 或 YYYY-MM-DD")
    return text


def _records(frame: pd.DataFrame) -> list[dict[str, Any]]:
    if frame is None or frame.empty:
        return []
    return [json_safe(record) for record in frame.to_dict("records")]


def _git_commit(root: Path) -> str | None:
    try:
        completed = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=root,
            capture_output=True,
            text=True,
            timeout=3,
        )
        if completed.returncode == 0:
            return completed.stdout.strip() or None
    except Exception:  # git 不可用时不影响回测
        logger.debug("读取 git commit 失败", exc_info=True)
    return None
