"""录制拦截器 - 录制请求/响应到文件"""
import json
import logging
import httpx
import asyncio
import typing
from .recorder import (
    write_request,
    write_response,
    get_recording_context as get_recording_ctx,
    clear_recording_context as clear_recording_ctx,
)
from .context import get_replay_id
from .transport import Middleware
import time


class TeeAsyncByteStream(httpx.AsyncByteStream):
    """旁路拦截流，迭代时复制数据，关闭时触发回调"""

    def __init__(self, original_stream: httpx.AsyncByteStream, on_close: typing.Callable[[list[bytes]], None], logger: logging.Logger = None):
        self.original_stream = original_stream
        self.on_close = on_close
        self.chunks: list[bytes] = []
        self.logger = logger
        self._iteration_started = False
        self._iteration_count = 0

    async def __aiter__(self) -> typing.AsyncIterator[bytes]:
        self._iteration_started = True
        self._iteration_count += 1
        if self.logger:
            self.logger.debug(f"[TeeStream] Starting iteration #{self._iteration_count}")
        try:
            async for chunk in self.original_stream:
                self.chunks.append(chunk)
                if self.logger:
                    self.logger.debug(f"[TeeStream] iteration #{self._iteration_count} yielded {len(chunk)} bytes")
                yield chunk
            if self.logger:
                self.logger.debug(f"[TeeStream] iteration #{self._iteration_count} completed, total chunks: {len(self.chunks)}")
        except Exception as e:
            if self.logger:
                self.logger.error(f"[TeeStream] iteration #{self._iteration_count} error: {e}")
            raise

    async def aclose(self) -> None:
        if self.logger:
            self.logger.debug(f"[TeeStream] aclose() called, chunks collected: {len(self.chunks)}")
        await self.original_stream.aclose()
        try:
            self.on_close(self.chunks)
            if self.logger:
                self.logger.debug(f"[TeeStream] on_close callback completed")
        except Exception as e:
            if self.logger:
                self.logger.error(f"[TeeStream] on_close callback error: {e}")


class TransportRecordingMiddleware(Middleware):
    """录制中间件 - 录制后端的请求/响应"""

    def __init__(self, logger: logging.Logger):
        super().__init__(logger)

    @staticmethod
    def _get_content_type(response: httpx.Response) -> str:
        content_type = response.headers.get("content-type", "")
        if isinstance(content_type, bytes):
            return content_type.decode("utf-8", errors="replace")
        return content_type.lower()

    async def __call__(self, request: httpx.Request, next_handler: typing.Callable[[], typing.Awaitable[httpx.Response]]) -> httpx.Response:
        """中间件处理逻辑"""
        if get_replay_id():
            return await next_handler()

        recording_ctx = get_recording_ctx()
        if not recording_ctx:
            self.logger.debug("[Recording] __call__: no recording context")
            return await next_handler()

        prefix = recording_ctx.get("prefix")
        suffix = recording_ctx.get("suffix")

        if not prefix or not suffix:
            self.logger.debug(f"[Recording] __call__: missing prefix or suffix, prefix={prefix}, suffix={suffix}")
            return await next_handler()

        self.logger.info(f"[Recording] __call__: {request.method} {request.url}")

        request_type = recording_ctx.get("request_type", "request").replace("client", "backend")

        headers = dict(request.headers)
        body = None
        if request.content:
            try:
                body = json.loads(request.content.decode('utf-8'))
            except json.JSONDecodeError:
                body = {"_raw": request.content.decode('utf-8', errors='replace')}
            except Exception:
                body = {"_raw": request.content.decode('utf-8', errors='replace')}

        write_request(
            prefix=prefix,
            suffix=suffix,
            request_type=request_type,
            endpoint=str(request.url.path),
            method=request.method,
            url=str(request.url),
            headers=headers,
            body=body
        )

        start_time = time.perf_counter()

        try:
            response = await next_handler()
            timing_ms = (time.perf_counter() - start_time) * 1000
        except Exception as error:
            timing_ms = (time.perf_counter() - start_time) * 1000
            self.logger.error(f"[Recording] on_error: {error}")
            response_type = recording_ctx.get("response_type", "response").replace("client", "backend")
            write_response(
                prefix=prefix,
                suffix=suffix,
                response_type=response_type,
                status_code=0,
                timing_ms=timing_ms,
                error=str(error)
            )
            clear_recording_ctx()
            raise

        response_type = recording_ctx.get("response_type", "response").replace("client", "backend")
        status_code = response.status_code

        chunks = None
        parsed_body = None
        content_type = self._get_content_type(response)

        if "text/event-stream" in content_type:
            # 捕获闭包所需的上下文变量
            ctx_prefix = prefix
            ctx_suffix = suffix
            ctx_response_type = response_type
            ctx_status_code = status_code
            ctx_timing_ms = timing_ms

            def on_stream_close(collected_chunks: list[bytes]) -> None:
                try:
                    response_body = b"".join(collected_chunks)
                    chunks_list = []
                    for chunk in response_body.split(b'\n'):
                        if chunk:
                            chunks_list.append(chunk.decode('utf-8', errors='replace'))

                    self.logger.info(f"[Recording] Stream closed, collected {len(chunks_list)} chunks")
                    write_response(
                        prefix=ctx_prefix,
                        suffix=ctx_suffix,
                        response_type=ctx_response_type,
                        status_code=ctx_status_code,
                        timing_ms=ctx_timing_ms,
                        chunks=chunks_list,
                        error=None
                    )
                    # 流结束后清除 context
                    clear_recording_ctx()
                except Exception as e:
                    self.logger.warning(f"录制流式响应失败: {e}")
                    clear_recording_ctx()

            # 替换原始的 stream 为旁路拦截器
            self.logger.info(f"[Recording] Wrapping stream with TeeAsyncByteStream, content-type: {content_type}")
            response.stream = TeeAsyncByteStream(response.stream, on_stream_close, logger=self.logger)
            return response

        try:
            response_body = await response.aread()

            if response_body:
                if isinstance(response_body, str):
                    response_body = response_body.encode('utf-8')

                try:
                    parsed_body = json.loads(response_body.decode('utf-8'))
                except (json.JSONDecodeError, UnicodeDecodeError):
                    parsed_body = {"_raw": response_body.decode('utf-8', errors='replace')}

        except RuntimeError as e:
            if "sync iterator" in str(e) or "async stream" in str(e):
                self.logger.debug(f"录制拦截器跳过流式响应读取: {e}")
            else:
                self.logger.warning(f"录制拦截器处理响应失败: {e}")
        except Exception as e:
            self.logger.warning(f"录制拦截器处理响应失败: {e}")

        write_response(
            prefix=prefix,
            suffix=suffix,
            response_type=response_type,
            status_code=status_code,
            timing_ms=timing_ms,
            body=parsed_body,
            chunks=chunks,
            error=None
        )
        # 清除录制上下文
        clear_recording_ctx()

        return response
