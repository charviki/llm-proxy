"""上游客户端适配层 - 统一原生流与非流式 JSON 的内部表示"""
import asyncio
import json
import logging
from dataclasses import dataclass
from functools import wraps
from typing import AsyncGenerator, Optional, TypeAlias

import httpx

from .stream import StreamSimulator


@dataclass
class UpstreamSSEEvent:
    """统一事件流里的 SSE 事件。"""

    event_lines: list[str]

    def has_non_data_lines(self) -> bool:
        """判断当前事件是否包含非 data 行。"""
        return any(not line.startswith("data:") for line in self.event_lines if not line.startswith(":"))

    def data_content(self) -> Optional[str]:
        """提取 data 行的 payload，并按 SSE 规则用换行拼接多行 data。"""
        data_lines = [
            line[len("data:"):].lstrip()
            for line in self.event_lines
            if line.startswith("data:")
        ]
        if not data_lines:
            return None
        return "\n".join(data_lines)

    def is_done(self) -> bool:
        """判断当前事件是否为流式终止标记。"""
        return self.data_content() == "[DONE]"


@dataclass
class UpstreamBodyChunk:
    """统一事件流里的原始 body 块。"""

    body_bytes: bytes


UpstreamStreamItem: TypeAlias = UpstreamSSEEvent | UpstreamBodyChunk


@dataclass
class UpstreamResponse:
    """上游响应的统一封装。

    - events: 用于流式消费
    - json_body/text_body: 用于直接返回错误或非流式结果
    - source_json: 保留原始 JSON，供非流式聚合时复用元数据
    """

    status_code: int
    events: Optional[AsyncGenerator[UpstreamStreamItem, None]] = None
    json_body: Optional[dict] = None
    text_body: Optional[str] = None
    source_json: Optional[dict] = None

    @classmethod
    def stream(
        cls,
        events: AsyncGenerator[UpstreamStreamItem, None],
        source_json: Optional[dict] = None,
        status_code: int = 200,
    ):
        return cls(status_code=status_code, events=events, source_json=source_json)

    @classmethod
    def json(cls, status_code: int, body: dict):
        return cls(status_code=status_code, json_body=body)

    @classmethod
    def text(cls, status_code: int, body: str):
        return cls(status_code=status_code, text_body=body)


def async_retry(max_attempts: int = 10, backoff_factor: float = 1.0):
    """给上游请求提供与旧 handler 一致的重试语义。"""

    def decorator(func):
        @wraps(func)
        async def wrapper(*args, **kwargs):
            logger: logging.Logger = kwargs.pop("logger")
            endpoint: str = kwargs.pop("endpoint")
            last_exception = None
            for attempt in range(max_attempts):
                try:
                    return await func(*args, **kwargs)
                except (httpx.HTTPStatusError, httpx.RequestError, asyncio.TimeoutError) as e:
                    last_exception = e
                    if attempt < max_attempts - 1:
                        delay = backoff_factor * (2 ** attempt)
                        if isinstance(e, httpx.HTTPStatusError) and not (500 <= e.response.status_code < 600):
                            raise
                        logger.warning(f"[{endpoint}] 请求失败 (attempt {attempt + 1}/{max_attempts}), {delay:.1f}s 后重试: {e}")
                        await asyncio.sleep(delay)
                    else:
                        logger.error(f"[{endpoint}] 请求失败，已达最大重试次数 ({max_attempts}): {e}")
                        raise
            raise last_exception
        return wrapper
    return decorator


class BackendClient:
    """位于 handler 与 httpx client 之间的上游客户端适配层。"""

    def __init__(self, client: httpx.AsyncClient, logger: logging.Logger):
        self._client = client
        self.logger = logger

    @property
    def raw_client(self) -> httpx.AsyncClient:
        return self._client

    async def request(
        self,
        req_json: dict,
        headers: dict,
        target_url: str,
        endpoint: str,
        requested_model_id: str,
        client_requested_stream: bool,
        backend_supports_stream: bool,
    ) -> UpstreamResponse:
        """统一发起上游请求，并把结果收敛为 UpstreamResponse。

        原生流式响应直接转成内部事件流；
        非流式 JSON 响应会先拉全量结果，再转换为内部事件流，
        这样下游只需要维护一套处理链。
        """
        if client_requested_stream and backend_supports_stream:
            return await self._request_native_stream(req_json, headers, target_url, endpoint)

        response = await self._post_with_retry(
            target_url,
            json=req_json,
            headers=headers,
            logger=self.logger,
            endpoint=endpoint,
        )
        if response.status_code != 200:
            try:
                return UpstreamResponse.json(status_code=response.status_code, body=response.json())
            except Exception:
                return UpstreamResponse.text(status_code=response.status_code, body=response.text)

        try:
            response_json = response.json()
        except Exception as e:
            self.logger.warning(f"[{endpoint}] 解析响应JSON失败: {e}, 状态码: {response.status_code}, 响应文本: {response.text[:200]}")
            return UpstreamResponse.json(
                status_code=502,
                body={"error": f"上游服务返回了非JSON格式的响应，状态码: {response.status_code}, 响应: {response.text[:100]}..."},
            )

        self.logger.debug(f"[{endpoint}] 响应体: {json.dumps(response_json, ensure_ascii=False)}")
        return UpstreamResponse.stream(
            events=self._simulate_upstream_events(
                response_json=response_json,
                endpoint=endpoint,
                requested_model_id=requested_model_id,
            ),
            source_json=response_json,
        )

    @async_retry(max_attempts=10, backoff_factor=1.0)
    async def _post_with_retry(self, url: str, **kwargs) -> httpx.Response:
        """非流式请求的统一重试入口。"""
        return await self._client.post(url, **kwargs)

    async def _request_native_stream(
        self,
        req_json: dict,
        headers: dict,
        target_url: str,
        endpoint: str,
    ) -> UpstreamResponse:
        """在返回 StreamingResponse 之前先确认上游状态码。"""
        last_exception = None

        for attempt in range(10):
            response_cm = self._client.stream("POST", target_url, json=req_json, headers=headers)
            response = None
            close_response = True
            try:
                response = await response_cm.__aenter__()
                if response.status_code != 200:
                    body_bytes = await response.aread()
                    try:
                        return UpstreamResponse.json(
                            status_code=response.status_code,
                            body=json.loads(body_bytes.decode("utf-8")),
                        )
                    except Exception:
                        return UpstreamResponse.text(
                            status_code=response.status_code,
                            body=body_bytes.decode("utf-8", errors="replace"),
                        )

                close_response = False
                return UpstreamResponse.stream(
                    status_code=response.status_code,
                    events=self._iterate_stream_events(response, response_cm, endpoint),
                )
            except (httpx.HTTPStatusError, httpx.RequestError, asyncio.TimeoutError) as e:
                last_exception = e
                if attempt < 9:
                    delay = 1.0 * (2 ** attempt)
                    if isinstance(e, httpx.HTTPStatusError) and not (500 <= e.response.status_code < 600):
                        self.logger.warning(f"[{endpoint}] 请求失败（不重试）: {e}")
                        break
                    self.logger.warning(f"[{endpoint}] 请求失败 (attempt {attempt + 1}/10), {delay:.1f}s 后重试: {e}")
                    await asyncio.sleep(delay)
                else:
                    self.logger.error(f"[{endpoint}] 请求失败，已达最大重试次数 (10): {e}")
            except Exception as e:
                last_exception = e
                self.logger.exception(f"[{endpoint}] 转发流式请求发生未知错误: {e}")
                break
            finally:
                if response is not None and close_response:
                    await response_cm.__aexit__(None, None, None)

        return UpstreamResponse.json(
            status_code=502,
            body={
                "error": {
                    "message": f"Proxy error: {str(last_exception)}",
                    "type": "server_error",
                    "code": "proxy_error",
                }
            },
        )

    async def _iterate_stream_events(
        self,
        response: httpx.Response,
        response_cm,
        endpoint: str,
    ) -> AsyncGenerator[UpstreamStreamItem, None]:
        """把原生 SSE 按事件边界拆成统一事件流。"""
        full_response_chunks = []
        try:
            is_debug = self.logger.isEnabledFor(logging.DEBUG)
            pending_event_lines: list[str] = []
            async for line in response.aiter_lines():
                if is_debug:
                    full_response_chunks.append(line.encode("utf-8") + b"\n")
                if line:
                    pending_event_lines.append(line)
                    continue
                if pending_event_lines:
                    yield UpstreamSSEEvent(event_lines=pending_event_lines)
                    pending_event_lines = []

            if pending_event_lines:
                yield UpstreamSSEEvent(event_lines=pending_event_lines)
        finally:
            try:
                await response_cm.__aexit__(None, None, None)
            finally:
                if full_response_chunks:
                    try:
                        full_response_bytes = b"".join(full_response_chunks)
                        full_response_str = full_response_bytes.decode("utf-8", errors="replace")
                        self.logger.debug(f"[{endpoint}] 完整流式响应内容:\n{full_response_str}")
                    except Exception as e:
                        self.logger.warning(f"[{endpoint}] 记录完整流式响应失败: {e}")

    async def _simulate_upstream_events(
        self,
        response_json: dict,
        endpoint: str,
        requested_model_id: str,
    ) -> AsyncGenerator[UpstreamStreamItem, None]:
        """把非流式 JSON 转成统一事件流。

        chat/completions 使用更细粒度的 payload 生成器，
        以便下游复用同一套 converter、语义合包和 SSE 输出逻辑。
        completions 仍沿用现有模拟流生成器。
        """
        model_id = response_json.get("model", requested_model_id)
        if endpoint == "chat/completions":
            try:
                for payload in StreamSimulator.iter_chat_completion_chunk_payloads(
                    response_json,
                    model_id,
                    fine_grained=True,
                ):
                    yield UpstreamSSEEvent(
                        event_lines=[
                            f"data: {json.dumps(payload, ensure_ascii=False, separators=(',', ':'))}"
                        ],
                    )
                yield UpstreamSSEEvent(event_lines=["data: [DONE]"])
                return
            except Exception as e:
                self.logger.exception(f"[{endpoint}] 模拟 chat 事件流失败: {e}")
                error_data = json.dumps({
                    "error": {
                        "message": f"Proxy error: failed to simulate stream: {str(e)}",
                        "type": "server_error",
                        "code": "proxy_stream_simulation_error",
                    }
                }, ensure_ascii=False)
                yield UpstreamSSEEvent(event_lines=[f"data: {error_data}"])
                return
        else:
            generator = StreamSimulator.simulate_completions(response_json, model_id, self.logger)

        async for chunk in generator:
            text = chunk.decode("utf-8")
            event_text = text[:-2] if text.endswith("\n\n") else text.rstrip("\n")
            yield UpstreamSSEEvent(event_lines=event_text.splitlines())
