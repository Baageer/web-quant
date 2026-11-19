"""FastAPI 应用入口。"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from app.api.routes_data import router as data_router
from app.api.routes_kline import router as kline_router
from app.api.routes_market import router as market_router
from app.api.routes_radar import router as radar_router
from app.core.config import get_settings
from app.core.errors import AppError
from app.core.logging import configure_logging, get_logger

settings = get_settings()
configure_logging(settings.log_level)
logger = get_logger(__name__)

app = FastAPI(
    title="web-quant",
    description="A 股量化研究服务：行情、机会雷达、K 线与交易信号",
    version="0.1.0-p2",
)

app.add_middleware(
    CORSMiddleware,
    allow_origin_regex=r"http://(localhost|127\.0\.0\.1):\d+",
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(market_router)
app.include_router(radar_router)
app.include_router(kline_router)
app.include_router(data_router)


@app.exception_handler(AppError)
async def app_error_handler(request: Request, exc: AppError) -> JSONResponse:
    return JSONResponse(
        status_code=exc.status_code,
        content={
            "ok": False,
            "error": exc.code,
            "message": exc.message,
            "warnings": [],
            "detail": exc.detail,
        },
    )


@app.exception_handler(Exception)
async def unexpected_error_handler(request: Request, exc: Exception) -> JSONResponse:
    logger.exception("未处理异常：%s %s", request.method, request.url.path)
    return JSONResponse(
        status_code=500,
        content={
            "ok": False,
            "error": "INTERNAL_ERROR",
            "message": "服务内部错误",
            "warnings": [],
        },
    )


@app.get("/api/health")
def health() -> dict:
    return {
        "ok": True,
        "service": "web-quant",
        "mode": settings.mode.value,
        "time": datetime.now().astimezone().isoformat(timespec="seconds"),
    }


def _mount_frontend() -> None:
    dist: Path = settings.frontend_dist
    assets = dist / "assets"
    if not dist.exists():
        logger.info("未找到前端构建产物：%s", dist)
        return
    if assets.exists():
        app.mount("/assets", StaticFiles(directory=assets), name="assets")

    @app.get("/{full_path:path}", include_in_schema=False)
    def spa(full_path: str):
        if full_path.startswith(("api/", "docs", "redoc", "openapi.json")):
            return JSONResponse(status_code=404, content={"ok": False, "error": "NOT_FOUND"})
        candidate = dist / full_path
        if full_path and candidate.is_file():
            return FileResponse(candidate)
        return FileResponse(dist / "index.html")


_mount_frontend()
