"""FastAPI Middleware - 录制客户端请求/响应"""
import json
import time
import logging
import fnmatch
from typing import Callable
from fastapi import Request

from .recorder import (
    generate_prefix,
    write_request,
    write_response,
    set_recording_context,
    clear_recording_context,
    _now_iso,
)
from .context import (
    set_replay_id,
    clear_replay_id,
)


class RecordingMiddleware:
    """
    一个原生 ASGI 中间件，用于拦截、清洗、保存请求与响应的数据（包括流式响应）。
    """
    def __init__(self, app, config, logger: logging.Logger):
        self.app = app
        self.config = config
        self.logger = logger

    async def __call__(self, scope: dict, receive: Callable, send: Callable) -> None:
        if scope["type"] != "http":
            return await self.app(scope, receive, send)

        # 构造请求对象
        request = Request(scope, receive)
        
        try:
            # 设置 replay_id（如果存在）
            replay_id = request.headers.get("X-Replay-Id")
            if replay_id:
                set_replay_id(replay_id)
            
            # 验证是否在配置的录制路径中
            record_paths = self.config.recording.record_paths
            
            # 检查当前请求路径是否匹配配置中的任意一个通配符模式
            is_path_matched = any(fnmatch.fnmatch(request.url.path, pattern) for pattern in record_paths)
            
            # 如果有 replay_id，跳过录制
            if replay_id and is_path_matched:
                return await self.app(scope, receive, send)
            
            if not is_path_matched:
                return await self.app(scope, receive, send)

            # --- 1. 读取并录制客户端请求 ---
            try:
                body_bytes = await request.body()
                body_json = json.loads(body_bytes.decode("utf-8")) if body_bytes else None
            except Exception as e:
                self.logger.warning(f"Failed to parse request body: {e}")
                body_json = None
                body_bytes = b""

            body_sent = False

            async def replay_receive() -> dict:
                nonlocal body_sent
                if not body_sent:
                    body_sent = True
                    return {"type": "http.request", "body": body_bytes, "more_body": False}
                return await receive()

            prefix, suffix = generate_prefix(request.url.path)
            
            write_request(
                prefix=prefix,
                suffix=suffix,
                request_type="client_request",
                endpoint=request.url.path,
                method=request.method,
                url=str(request.url),
                headers=dict(request.headers),
                body=body_json
            )

            # 设置上下文，供 ProxyTransport 拦截器使用
            ctx = {
                "prefix": prefix,
                "suffix": suffix,
                "request_type": "client_request",
                "response_type": "client_response",
            }
            set_recording_context(ctx)

            # 拦截 send 记录响应
            chunks = []
            status_code = 200
            start_time = time.time()
            headers = {}
            request_received_at = _now_iso()
            response_start_at = None

            async def custom_send(message: dict) -> None:
                nonlocal status_code, headers, response_start_at
                if message["type"] == "http.response.start":
                    status_code = message.get("status", 200)
                    if response_start_at is None:
                        response_start_at = _now_iso()
                    for k, v in message.get("headers", []):
                        try:
                            key = k.decode("utf-8") if isinstance(k, bytes) else str(k)
                            val = v.decode("utf-8") if isinstance(v, bytes) else str(v)
                            headers[key.lower()] = val
                        except Exception:
                            pass
                elif message["type"] == "http.response.body":
                    if "body" in message and message["body"]:
                        chunks.append(message["body"])
                await send(message)

            try:
                await self.app(scope, replay_receive, custom_send)
            except Exception as e:
                timing_ms = (time.time() - start_time) * 1000
                self.logger.error(f"Request failed: {e}")
                write_response(
                    prefix=prefix,
                    suffix=suffix,
                    response_type="client_response",
                    status_code=500,
                    timing_ms=timing_ms,
                    chunks=[],
                    error=str(e),
                    timing={
                        "request_received_at": request_received_at,
                        "response_start_at": response_start_at,
                    }
                )
                raise
            else:
                timing_ms = (time.time() - start_time) * 1000
                response_completed_at = _now_iso()
                decoded_chunks = []
                for c in chunks:
                    if isinstance(c, bytes):
                        decoded_chunks.append(c.decode("utf-8", errors="replace"))
                    elif isinstance(c, str):
                        decoded_chunks.append(c)
                    else:
                        decoded_chunks.append(str(c))

                content_type = headers.get("content-type", "")
                is_stream = "text/event-stream" in content_type

                client_timing = {
                    "request_received_at": request_received_at,
                    "response_start_at": response_start_at,
                    "response_completed_at": response_completed_at,
                }
                        
                if not is_stream and decoded_chunks:
                    try:
                        full_body = "".join(decoded_chunks)
                        json_body = json.loads(full_body)
                        write_response(
                            prefix=prefix,
                            suffix=suffix,
                            response_type="client_response",
                            status_code=status_code,
                            timing_ms=timing_ms,
                            body=json_body,
                            error=None,
                            headers=headers,
                            timing=client_timing
                        )
                    except Exception:
                        write_response(
                            prefix=prefix,
                            suffix=suffix,
                            response_type="client_response",
                            status_code=status_code,
                            timing_ms=timing_ms,
                            chunks=decoded_chunks,
                            error=None,
                            headers=headers,
                            timing=client_timing
                        )
                else:
                    write_response(
                        prefix=prefix,
                        suffix=suffix,
                        response_type="client_response",
                        status_code=status_code,
                        timing_ms=timing_ms,
                        chunks=decoded_chunks,
                        error=None,
                        headers=headers,
                        timing=client_timing
                    )
        finally:
            clear_recording_context()
            clear_replay_id()
