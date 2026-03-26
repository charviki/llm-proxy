"""拦截器接口定义"""
from typing import Protocol, Optional
import httpx


class Interceptor(Protocol):
    """拦截器接口 - 可插拔的请求/响应拦截组件"""

    async def on_request(self, request: httpx.Request, ctx: dict) -> None:
        """请求发送前调用"""
        ...

    async def on_response(self, response: httpx.Response, ctx: dict, timing_ms: float) -> None:
        """响应接收后调用"""
        ...

    async def on_error(self, error: Exception, ctx: dict, timing_ms: float) -> None:
        """请求出错时调用"""
        ...
