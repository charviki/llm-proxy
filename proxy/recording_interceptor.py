"""录制拦截器 - 录制请求/响应到文件"""
import json
import logging
import httpx

from .recorder import (
    write_request,
    write_response,
    get_recording_context,
)


class RecordingInterceptor:
    """录制拦截器 - 录制后端的请求/响应"""

    def __init__(self, logger: logging.Logger):
        self.logger = logger

    async def on_request(self, request: httpx.Request, ctx: dict) -> None:
        """录制后端请求"""
        recording_ctx = get_recording_context()
        if not recording_ctx:
            return

        prefix = recording_ctx.get("prefix")
        suffix = recording_ctx.get("suffix")

        if not prefix or not suffix:
            return

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

    async def on_response(self, response: httpx.Response, ctx: dict, timing_ms: float) -> None:
        """录制后端响应"""
        recording_ctx = get_recording_context()
        if not recording_ctx:
            return

        prefix = recording_ctx.get("prefix")
        suffix = recording_ctx.get("suffix")

        if not prefix or not suffix:
            return

        response_type = recording_ctx.get("response_type", "response").replace("client", "backend")

        status_code = response.status_code

        chunks = None
        parsed_body = None

        try:
            # 先调用 aread() 确保响应体被完全读取
            response_body = await response.aread()

            if response_body:
                # 确保 response_body 是 bytes
                if isinstance(response_body, str):
                    response_body = response_body.encode('utf-8')

                # 检查 content-type
                content_type = response.headers.get('content-type', '')
                if isinstance(content_type, bytes):
                    content_type = content_type.decode('utf-8', errors='replace')

                # 检查是否是 SSE
                is_sse = 'text/event-stream' in content_type or b'[DONE]' in response_body

                if is_sse:
                    # SSE 流式响应
                    chunks = []
                    for chunk in response_body.split(b'\n'):
                        if chunk:
                            decoded = chunk.decode('utf-8', errors='replace')
                            chunks.append(decoded)
                else:
                    # 非流式响应，尝试解析 JSON
                    try:
                        parsed_body = json.loads(response_body.decode('utf-8'))
                    except (json.JSONDecodeError, UnicodeDecodeError):
                        parsed_body = {"_raw": response_body.decode('utf-8', errors='replace')}

        except RuntimeError as e:
            # 流式响应可能无法在这里读取，跳过录制响应体
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

    async def on_error(self, error: Exception, ctx: dict, timing_ms: float) -> None:
        """录制后端错误"""
        recording_ctx = get_recording_context()
        if not recording_ctx:
            return

        prefix = recording_ctx.get("prefix")
        suffix = recording_ctx.get("suffix")

        if not prefix or not suffix:
            return

        response_type = recording_ctx.get("response_type", "response").replace("client", "backend")

        write_response(
            prefix=prefix,
            suffix=suffix,
            response_type=response_type,
            status_code=0,
            timing_ms=timing_ms,
            error=str(error)
        )
