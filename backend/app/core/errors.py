"""统一错误码与异常。"""

from __future__ import annotations

from typing import Any


class AppError(Exception):
    code = "INTERNAL_ERROR"
    status_code = 500

    def __init__(self, message: str, *, detail: Any = None) -> None:
        super().__init__(message)
        self.message = message
        self.detail = detail


class DataNotReadyError(AppError):
    code = "DATA_NOT_READY"
    status_code = 404


class SymbolNotFoundError(AppError):
    code = "SYMBOL_NOT_FOUND"
    status_code = 404


class InsufficientHistoryError(AppError):
    code = "INSUFFICIENT_HISTORY"
    status_code = 422


class InvalidParamError(AppError):
    code = "INVALID_PARAM"
    status_code = 400


class ModeNotAllowedError(AppError):
    code = "MODE_NOT_ALLOWED"
    status_code = 409


class TushareConfigError(AppError):
    code = "TUSHARE_NOT_CONFIGURED"
    status_code = 503


class TusharePermissionError(AppError):
    code = "TUSHARE_PERMISSION"
    status_code = 403


class TushareRateLimitError(AppError):
    code = "TUSHARE_RATE_LIMITED"
    status_code = 429


class TushareApiError(AppError):
    code = "TUSHARE_API_ERROR"
    status_code = 502
