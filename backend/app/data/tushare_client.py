"""TuShare Pro HTTP 客户端。

直接 POST https://api.tushare.pro，支持分页、限流、重试和错误码映射。
参考 quant_system/data/fetch_tushare_prices.py 的实现思路。
"""

from __future__ import annotations

import json
import threading
import time
import urllib.error
import urllib.request
from typing import Any, Mapping

import pandas as pd

from app.core.errors import (
    TushareApiError,
    TushareConfigError,
    TusharePermissionError,
    TushareRateLimitError,
)
from app.core.logging import get_logger

logger = get_logger(__name__)

API_URL = "https://api.tushare.pro"
PAGE_SIZE = 6000

DAILY_FIELDS = "ts_code,trade_date,open,high,low,close,pre_close,change,pct_chg,vol,amount"
DAILY_BASIC_FIELDS = (
    "ts_code,trade_date,turnover_rate,turnover_rate_f,volume_ratio,"
    "pe,pe_ttm,pb,ps,ps_ttm,dv_ratio,dv_ttm,total_share,float_share,free_share,total_mv,circ_mv"
)
ADJ_FACTOR_FIELDS = "ts_code,trade_date,adj_factor"
INDEX_DAILY_FIELDS = "ts_code,trade_date,close,open,high,low,pre_close,change,pct_chg,vol,amount"
MONEYFLOW_FIELDS = (
    "ts_code,trade_date,buy_sm_vol,buy_sm_amount,sell_sm_vol,sell_sm_amount,"
    "buy_md_vol,buy_md_amount,sell_md_vol,sell_md_amount,buy_lg_vol,buy_lg_amount,"
    "sell_lg_vol,sell_lg_amount,buy_elg_vol,buy_elg_amount,sell_elg_vol,sell_elg_amount,net_mf_vol,net_mf_amount"
)
LIMIT_LIST_FIELDS = "trade_date,ts_code,industry,name,close,pct_chg,amount,limit_amount,float_mv,total_mv,turnover_ratio,fd_amount,first_time,last_time,open_times,up_stat,limit_times,limit"
SUSPEND_FIELDS = "ts_code,trade_date,suspend_type,suspend_timing"
STOCK_BASIC_FIELDS = "ts_code,symbol,name,area,industry,market,list_date,list_status,delist_date"
TRADE_CAL_FIELDS = "exchange,cal_date,is_open,pretrade_date"
FINA_INDICATOR_FIELDS = (
    "ts_code,ann_date,end_date,roe,roe_waa,roe_dt,roe_yearly,roa,roic,"
    "grossprofit_margin,netprofit_margin,debt_to_assets,current_ratio,quick_ratio,"
    "netprofit_yoy,or_yoy"
)


class RateLimiter:
    """简单的等间隔限流器，保证每分钟调用数不超过配置值。"""

    def __init__(self, calls_per_minute: int) -> None:
        self._interval = 60.0 / max(calls_per_minute, 1)
        self._next_allowed = 0.0
        self._lock = threading.Lock()

    def acquire(self) -> None:
        with self._lock:
            now = time.monotonic()
            wait = self._next_allowed - now
            if wait > 0:
                time.sleep(wait)
                now = time.monotonic()
            self._next_allowed = now + self._interval


class TushareClient:
    def __init__(
        self,
        token: str,
        *,
        calls_per_minute: int = 120,
        max_retries: int = 5,
        timeout: int = 60,
    ) -> None:
        if not token:
            raise TushareConfigError("未配置 TS_TOKEN，无法访问 TuShare Pro")
        self._token = token
        self._max_retries = max(max_retries, 1)
        self._timeout = timeout
        self._limiter = RateLimiter(calls_per_minute)

    def call(
        self,
        api_name: str,
        params: Mapping[str, Any] | None = None,
        fields: str = "",
        *,
        page_size: int = PAGE_SIZE,
    ) -> pd.DataFrame:
        frames: list[pd.DataFrame] = []
        offset = 0
        while True:
            body = self._request(api_name, params or {}, fields, offset, page_size)
            data = body.get("data") or {}
            names = data.get("fields") or []
            items = data.get("items") or []
            if not items:
                break
            frames.append(pd.DataFrame(items, columns=names))
            if len(items) < page_size:
                break
            offset += page_size
        if not frames:
            return pd.DataFrame(columns=[f for f in fields.split(",") if f])
        return pd.concat(frames, ignore_index=True)

    def _request(
        self,
        api_name: str,
        params: Mapping[str, Any],
        fields: str,
        offset: int,
        page_size: int = PAGE_SIZE,
    ) -> dict[str, Any]:
        query = dict(params, limit=page_size, offset=offset)
        payload = json.dumps(
            {"api_name": api_name, "token": self._token, "params": query, "fields": fields}
        ).encode("utf-8")
        last_error: Exception | None = None
        for attempt in range(1, self._max_retries + 1):
            self._limiter.acquire()
            try:
                request = urllib.request.Request(
                    API_URL,
                    data=payload,
                    headers={"Content-Type": "application/json"},
                )
                with urllib.request.urlopen(request, timeout=self._timeout) as response:
                    body = json.loads(response.read().decode("utf-8"))
            except (urllib.error.URLError, TimeoutError, OSError) as exc:
                last_error = exc
                logger.warning("%s 网络错误（第 %s 次）：%s", api_name, attempt, exc)
                time.sleep(min(2 ** attempt, 30))
                continue

            code = body.get("code")
            if code == 0:
                return body

            message = str(body.get("msg") or body.get("message") or "")
            if "积分" in message or "权限" in message:
                raise TusharePermissionError(f"{api_name}: {message}")
            if "每分钟" in message or "频率" in message or "限制" in message:
                logger.warning("%s 触发频率限制，等待后重试", api_name)
                time.sleep(61)
                last_error = TushareRateLimitError(f"{api_name}: {message}")
                continue

            last_error = TushareApiError(f"{api_name}: {message}")
            logger.warning("%s 接口错误（第 %s 次）：%s", api_name, attempt, message)
            time.sleep(min(2 ** attempt, 30))

        if isinstance(last_error, TusharePermissionError):
            raise last_error
        raise TushareApiError(f"{api_name} 重试 {self._max_retries} 次后仍失败：{last_error}")

    # ---- 具体接口 ----

    def stock_basic(self, list_status: str = "L") -> pd.DataFrame:
        frame = self.call(
            "stock_basic",
            {"list_status": list_status},
            STOCK_BASIC_FIELDS,
        )
        return frame

    def trade_cal(self, start_date: str, end_date: str) -> pd.DataFrame:
        return self.call(
            "trade_cal",
            {"exchange": "SSE", "start_date": start_date, "end_date": end_date},
            TRADE_CAL_FIELDS,
        )

    def daily(self, trade_date: str) -> pd.DataFrame:
        return self.call("daily", {"trade_date": trade_date}, DAILY_FIELDS)

    def daily_basic(self, trade_date: str) -> pd.DataFrame:
        return self.call("daily_basic", {"trade_date": trade_date}, DAILY_BASIC_FIELDS)

    def adj_factor(self, trade_date: str) -> pd.DataFrame:
        return self.call("adj_factor", {"trade_date": trade_date}, ADJ_FACTOR_FIELDS)

    def index_daily(self, ts_code: str, start_date: str, end_date: str) -> pd.DataFrame:
        return self.call(
            "index_daily",
            {"ts_code": ts_code, "start_date": start_date, "end_date": end_date},
            INDEX_DAILY_FIELDS,
        )

    def moneyflow(self, trade_date: str) -> pd.DataFrame:
        return self.call("moneyflow", {"trade_date": trade_date}, MONEYFLOW_FIELDS)

    def limit_list(self, trade_date: str, limit_type: str = "U") -> pd.DataFrame:
        return self.call(
            "limit_list_d",
            {"trade_date": trade_date, "limit_type": limit_type},
            LIMIT_LIST_FIELDS,
        )

    def suspend(self, trade_date: str) -> pd.DataFrame:
        return self.call(
            "suspend_d",
            {"trade_date": trade_date, "suspend_type": "S"},
            SUSPEND_FIELDS,
        )

    def fina_indicator(
        self, ts_code: str, start_date: str, end_date: str
    ) -> pd.DataFrame:
        """按股票拉取财务指标；该接口单页最多返回 100 行，需分页。"""
        return self.call(
            "fina_indicator",
            {
                "ts_code": ts_code,
                "start_date": start_date,
                "end_date": end_date,
            },
            FINA_INDICATOR_FIELDS,
            page_size=100,
        )
