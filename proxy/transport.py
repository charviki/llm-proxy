"""httpx 代理传输层 - 可插拔的拦截器传输层"""
import time
import logging
from typing import Optional, Sequence
import httpx

from .interceptors import Interceptor


class ProxyTransport(httpx.AsyncHTTPTransport):
    """代理传输层 - 包装 httpx 传输层，支持拦截器扩展"""

    def __init__(self, logger: logging.Logger, interceptors: Optional[Sequence[Interceptor]] = None):
        super().__init__()
        self._app = httpx.AsyncHTTPTransport()
        self._interceptors = list(interceptors) if interceptors else []
        self.logger = logger

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        """处理请求，依次调用拦截器"""
        # 构建上下文
        ctx = {"request": request}

        # 请求前拦截
        for interceptor in self._interceptors:
            if hasattr(interceptor, 'on_request'):
                await interceptor.on_request(request, ctx)

        start_time = time.perf_counter()

        try:
            response = await self._app.handle_async_request(request)
            timing_ms = (time.perf_counter() - start_time) * 1000

            # 响应后拦截
            for interceptor in reversed(self._interceptors):
                if hasattr(interceptor, 'on_response'):
                    await interceptor.on_response(response, ctx, timing_ms)

            return response

        except Exception as e:
            timing_ms = (time.perf_counter() - start_time) * 1000

            # 错误拦截
            for interceptor in reversed(self._interceptors):
                if hasattr(interceptor, 'on_error'):
                    await interceptor.on_error(e, ctx, timing_ms)
            self.logger.exception(f"代理传输层处理请求失败: {e}")
            raise
