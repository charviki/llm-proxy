"""FastAPI Middleware - 录制客户端请求/响应"""
import json
import time
import logging
import uuid
from typing import Callable
from fastapi import Request

from .recorder import (
    generate_prefix,
    write_request,
    write_response,
    set_recording_context,
    clear_recording_context,
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
        
        # 验证是否在配置的录制路径中
        record_paths = self.config.recording.record_paths if self.config else ["/v1/chat/completions"]
        if request.url.path not in record_paths:
            return await self.app(scope, receive, send)

        # --- 1. 读取并录制客户端请求 ---
        try:
            body_bytes = await request.body()
            body_json = json.loads(body_bytes.decode("utf-8")) if body_bytes else None
        except Exception as e:
            self.logger.warning(f"Failed to parse request body: {e}")
            body_json = None
            body_bytes = b""

        # 构造一个可重放的 receive，因为上方的 request.body() 已经消费了原生的 receive
        async def replay_receive() -> dict:
            return {"type": "http.request", "body": body_bytes, "more_body": False}

        # 组装前缀：将 / 替换为 _，去除开头的下划线
        prefix = request.url.path.replace("/", "_").strip("_")
        if not prefix:
            prefix = "root"
            
        suffix = f"{int(time.time())}_{uuid.uuid4().hex[:6]}"
        
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

        async def custom_send(message: dict) -> None:
            nonlocal status_code, headers
            if message["type"] == "http.response.start":
                status_code = message.get("status", 200)
                # 防御性处理，有些中间件可能不规范
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
            # 传入 replay_receive 给下游
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
                error=str(e)
            )
            raise
        else:
            # 正常请求结束，写入响应数据
            timing_ms = (time.time() - start_time) * 1000
            decoded_chunks = []
            for c in chunks:
                if isinstance(c, bytes):
                    decoded_chunks.append(c.decode("utf-8", errors="replace"))
                elif isinstance(c, str):
                    decoded_chunks.append(c)
                else:
                    decoded_chunks.append(str(c))
                    
            # 检查 content-type 判断是否为流式
            content_type = headers.get("content-type", "")
            is_stream = "text/event-stream" in content_type
            
            # 兼容非流式时的上下文恢复（因为使用了原生 ASGI）
            # 上下文可能在生成流的过程中被清理了，不再重新设置
            
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
                        headers=headers
                    )
                except Exception:
                    # 解析失败也作为 chunks 记录
                    write_response(
                        prefix=prefix,
                        suffix=suffix,
                        response_type="client_response",
                        status_code=status_code,
                        timing_ms=timing_ms,
                        chunks=decoded_chunks,
                        error=None,
                        headers=headers
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
                    headers=headers
                )
        finally:
            clear_recording_context()
