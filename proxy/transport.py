"""自定义 Transport - 支持 Middleware 链和录制重放"""
from abc import ABC, abstractmethod
from typing import Callable, Awaitable, Optional
from pathlib import Path
import json
import logging

import httpx

from .context import get_replay_id


class Middleware(ABC):
    """中间件基类"""

    def __init__(self, logger: logging.Logger):
        self.logger = logger

    @abstractmethod
    async def __call__(
        self,
        request: httpx.Request,
        next_handler: Callable[[], Awaitable[httpx.Response]]
    ) -> httpx.Response:
        """中间件处理逻辑

        Args:
            request: HTTP 请求
            next_handler: 下一个处理器（中间件或真实请求）
        """
        pass


class ReplayMiddleware(Middleware):
    """重放中间件 - 根据 replay_id 返回录制的响应"""

    def __init__(self, recordings_dir: Path, logger: logging.Logger):
        super().__init__(logger)
        self.recordings_dir = recordings_dir

    async def __call__(self, request, next_handler):
        replay_id = get_replay_id()

        if not replay_id:
            return await next_handler()

        prefix = self._path_to_prefix(request.url.path)
        replay_file = self.recordings_dir / f"{prefix}__{replay_id}__backend_response.json"

        if not replay_file.exists():
            self.logger.warning(f"[Replay] 录制文件不存在: {replay_file}")
            return await next_handler()

        self.logger.info(f"[Replay] 使用录制重放: {replay_id}, prefix: {prefix}")
        return await self._create_mock_response(replay_file)

    @staticmethod
    def _path_to_prefix(path: str) -> str:
        """将请求路径转换为录制文件名用的 prefix

        例如:
            /v1/chat/completions → v1_chat_completions
            /v1/completions → v1_completions
        """
        return path.lstrip("/").replace("/", "_")

    async def _create_mock_response(self, replay_file: Path) -> httpx.Response:
        """从录制文件创建 Mock 响应"""
        try:
            with open(replay_file, 'r', encoding='utf-8') as f:
                data = json.load(f)

            status_code = data.get("status_code", 200)
            chunks = data.get("chunks")

            if chunks:
                return self._create_stream_response(status_code, chunks)
            else:
                body = data.get("body", {})
                return httpx.Response(status_code=status_code, json=body)
        except Exception as e:
            self.logger.error(f"[Replay] 创建 Mock 响应失败: {e}")
            return httpx.Response(status_code=500, json={"error": str(e)})

    def _create_stream_response(self, status_code: int, chunks: list[str]) -> httpx.Response:
        """创建流式 Mock 响应"""
        async def stream_content():
            for chunk in chunks:
                if chunk.startswith("data: "):
                    yield chunk.encode('utf-8') + b'\n\n'
                elif chunk == "[DONE]":
                    yield b'data: [DONE]\n\n'
                else:
                    yield chunk.encode('utf-8') + b'\n\n'

        return httpx.Response(
            status_code=status_code,
            content=stream_content(),
            headers={"content-type": "text/event-stream"}
        )


class ProxyTransport(httpx.AsyncBaseTransport):
    """统一的 Transport，支持中间件链"""

    def __init__(
        self,
        logger: logging.Logger,
        middlewares: Optional[list[Middleware]] = None
    ):
        self._real_transport = httpx.AsyncHTTPTransport()
        self._logger = logger
        self._middlewares = middlewares or []

    async def aclose(self) -> None:
        """关闭底层的真实传输层，释放连接池资源"""
        await self._real_transport.aclose()

    async def handle_async_request(self, request, **kwargs):
        async def inner(index: int = 0):
            if index >= len(self._middlewares):
                return await self._real_transport.handle_async_request(request, **kwargs)

            middleware = self._middlewares[index]

            async def next_handler():
                return await inner(index + 1)

            return await middleware(request, next_handler)

        return await inner()
