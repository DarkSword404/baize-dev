"""X-01: API 限流中间件 —— 基于 sliding window 的简单内存限流。

生产环境如需分布式限流，可替换为 Redis 后端。
"""

from __future__ import annotations

import time
from collections import defaultdict, deque
from typing import Optional

from fastapi import Request, Response
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware

__all__ = ["RateLimitMiddleware"]


class RateLimitMiddleware(BaseHTTPMiddleware):
    """按客户端 IP 限流。

    默认: 60 次/分钟（可通过环境变量 BAIZE_RATE_LIMIT 调整）。
    超限时返回 429。
    """

    def __init__(self, app, max_requests: int = 60, window_seconds: int = 60):
        super().__init__(app)
        self.max_requests = max_requests
        self.window_seconds = window_seconds
        self._hits: dict[str, deque[float]] = defaultdict(deque)

    async def dispatch(self, request: Request, call_next):
        # 健康检查不限流
        if request.url.path in ("/api/v1/health", "/api/v1/live", "/metrics"):
            return await call_next(request)

        client_ip = request.client.host if request.client else "unknown"
        now = time.monotonic()
        window = self._hits[client_ip]

        # 清除过期记录
        while window and window[0] < now - self.window_seconds:
            window.popleft()

        if len(window) >= self.max_requests:
            return JSONResponse(
                status_code=429,
                content={"detail": f"请求频率超限（{self.max_requests}/{self.window_seconds}s），请稍后重试。"},
            )

        window.append(now)
        return await call_next(request)
