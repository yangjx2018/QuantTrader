"""可选 API Key 鉴权中间件。

当 settings.API_KEY 非空时，对敏感写操作要求请求头 X-API-Key 匹配。
未配置 API_KEY 时放行（本地开发兼容），但会在日志中告警一次。
"""

from __future__ import annotations

import logging

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse, Response

from common.config import get_settings

logger = logging.getLogger(__name__)

_warned_open = False


def _is_protected(path: str, method: str) -> bool:
    method = method.upper()
    if method in {"OPTIONS", "HEAD"}:
        return False

    # 账户下单、统一路由下单、同步 —— 一律保护
    if path.startswith("/api/account/order") or path.startswith("/api/integration/order"):
        return method != "GET"
    if path.startswith("/api/integration/account/sync"):
        return method in {"POST", "PUT", "PATCH", "DELETE"}

    if not path.startswith("/api/execution"):
        return False

    # execution：写操作保护；GET 只读放行
    if method == "GET":
        return False
    return method in {"POST", "PUT", "PATCH", "DELETE"}


class ApiKeyAuthMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next) -> Response:
        global _warned_open
        settings = get_settings()
        api_key = (settings.API_KEY or "").strip()

        if not api_key:
            if not _warned_open:
                logger.warning(
                    "API_KEY 未配置：执行/下单接口处于开放模式。请在 .env 设置 API_KEY 以启用鉴权。"
                )
                _warned_open = True
            return await call_next(request)

        path = request.url.path
        if not _is_protected(path, request.method):
            return await call_next(request)

        provided = (
            request.headers.get("X-API-Key")
            or request.headers.get("x-api-key")
            or ""
        ).strip()
        if provided != api_key:
            return JSONResponse(
                status_code=401,
                content={
                    "success": False,
                    "data": None,
                    "message": "未授权：请提供正确的 X-API-Key",
                },
            )
        return await call_next(request)
