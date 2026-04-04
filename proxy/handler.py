"""OpenAI 代理处理器 - 核心代理逻辑"""
import asyncio
import logging
import json
from typing import AsyncGenerator, Optional
from fastapi import Request
from fastapi.responses import JSONResponse, Response, StreamingResponse

from config.models import BackendsConfig, SSECoalescingConfig
from .backend_client import BackendClient, UpstreamBodyChunk, UpstreamResponse, UpstreamStreamItem, UpstreamSSEEvent
from .converter import ChunkConverterMatcher
from .models import ModelsManager, Backend
from .response_assembler import (
    assemble_chat_completion_response,
    assemble_completion_response,
)
from .sse_coalescer import SSESemanticCoalescer
from .stream_processor import PROCESSING_MARKER, StreamEventProcessor

# 标准 SSE 响应头，防止中间层缓冲
_SSE_HEADERS = {
    "Cache-Control": "no-cache",
    "X-Accel-Buffering": "no",
    "Connection": "keep-alive",
}
# 对客户端可见的 SSE 注释行；内部 mask 直接复用这份字节值。
_PROCESSING_COMMENT = PROCESSING_MARKER


class ProxyHandler:
    """OpenAI 代理处理器"""

    def __init__(
        self,
        backends: BackendsConfig,
        logger: logging.Logger,
        parser_matcher: ChunkConverterMatcher,
        sse_coalescing_config: Optional[SSECoalescingConfig] = None,
    ):
        self.backends = backends
        self.logger = logger
        self.parser_matcher = parser_matcher
        self.sse_coalescing_config = sse_coalescing_config or SSECoalescingConfig()
        self.models_manager: Optional[ModelsManager] = None
        self._backend_client: Optional[BackendClient] = None

    async def set_client(self, client) -> None:
        """设置 HTTP 客户端并加载模型列表"""
        self._backend_client = BackendClient(client, self.logger)
        self.models_manager = ModelsManager(self.backends, self.logger)
        await self.models_manager.load_models(self._backend_client.raw_client)

    def select_backend(self, requested_model: str) -> Optional[Backend]:
        """根据模型 ID 查找后端"""
        if self.models_manager:
            return self.models_manager.find_backend(requested_model)
        return None

    async def handle_chat_completions(self, request: Request) -> Response:
        """处理 chat completions 请求"""
        return await self._handle_proxy(request, "chat/completions")

    async def handle_completions(self, request: Request) -> Response:
        """处理 completions 请求"""
        return await self._handle_proxy(request, "completions")

    async def _handle_proxy(self, request: Request, endpoint: str) -> Response:
        """通用的代理处理逻辑"""
        try:
            content_type = request.headers.get('Content-Type', '')
            if 'application/json' not in content_type:
                return JSONResponse(status_code=400, content={"error": "Content-Type必须为application/json"})

            try:
                req_json = await request.json()
                if req_json is None:
                    return JSONResponse(status_code=400, content={"error": "无效的JSON请求体"})
            except Exception as e:
                self.logger.exception(f"[{endpoint}] JSON解析失败: {str(e)}")
                return JSONResponse(status_code=400, content={"error": f"JSON解析失败: {str(e)}"})

            requested_model = req_json.get('model', '')
            backend = self.select_backend(requested_model)

            if not backend:
                self.logger.error(f"未找到匹配的模型: {requested_model}")
                return JSONResponse(status_code=400, content={"error": f"未找到匹配的模型: {requested_model}"})

            target_api_url = backend.endpoint.rstrip('/')
            target_model_id = backend.target_model_id.strip()
            custom_model_id = requested_model

            if 'model' in req_json:
                original_model = req_json['model']
                req_json['model'] = target_model_id
                self.logger.debug(f"[{endpoint}] 模型ID从 {original_model} 修改为 {target_model_id}")

            # 客户端是否请求了流式输出
            original_stream = req_json.get('stream', False)

            # 综合判断：只有当客户端请求流式，且后端也支持流式时，才向后端发送流式请求
            is_backend_stream = original_stream and backend.stream

            if 'stream' in req_json:
                req_json['stream'] = is_backend_stream

            headers = {"Content-Type": "application/json"}
            if backend.api_key:
                headers["Authorization"] = f"Bearer {backend.api_key}"

            target_url = f"{target_api_url}/v1/{endpoint}"
            self.logger.info(f"[{endpoint}] 转发请求到: {target_url} (stream: {is_backend_stream})")
            upstream_response = await self._backend_client.request(
                req_json=req_json,
                headers=headers,
                target_url=target_url,
                endpoint=endpoint,
                requested_model_id=custom_model_id,
                client_requested_stream=original_stream,
                backend_supports_stream=backend.stream,
            )
            return await self._build_response(
                upstream_response=upstream_response,
                endpoint=endpoint,
                custom_model_id=custom_model_id,
                original_stream=original_stream,
            )

        except Exception as e:
            self.logger.exception(f"[{endpoint}] 处理请求时发生内部错误: {str(e)}")
            return JSONResponse(status_code=500, content={"error": f"内部服务器错误: {str(e)}"})

    async def _build_response(
        self,
        upstream_response: UpstreamResponse,
        endpoint: str,
        custom_model_id: str,
        original_stream: bool,
    ) -> Response:
        if upstream_response.events is None:
            if upstream_response.json_body is not None:
                return JSONResponse(status_code=upstream_response.status_code, content=upstream_response.json_body)
            if upstream_response.text_body is not None:
                return Response(status_code=upstream_response.status_code, content=upstream_response.text_body)
            return Response(status_code=upstream_response.status_code)

        if original_stream:
            return self._create_streaming_response(
                upstream_events=upstream_response.events,
                endpoint=endpoint,
                custom_model_id=custom_model_id,
            )

        response_json = await self._aggregate_response_json(
            upstream_events=upstream_response.events,
            endpoint=endpoint,
            custom_model_id=custom_model_id,
            source_json=upstream_response.source_json,
        )
        return JSONResponse(content=response_json)

    def _create_streaming_response(
        self,
        upstream_events: AsyncGenerator[UpstreamStreamItem, None],
        endpoint: str,
        custom_model_id: str,
    ) -> StreamingResponse:
        async def stream_generator():
            processing_delay_ms = self.sse_coalescing_config.processing_delay_ms
            converter = self.parser_matcher.get_parser(custom_model_id)
            coalescer = None
            if endpoint == "chat/completions":
                coalescer = SSESemanticCoalescer(self.sse_coalescing_config)
            processor = StreamEventProcessor(
                coalescer=coalescer,
                processing_enabled=processing_delay_ms is not None,
            )
            async for upstream_event in upstream_events:
                if isinstance(upstream_event, UpstreamBodyChunk):
                    for chunk in processor.flush_pending():
                        yield chunk
                    yield upstream_event.body_bytes
                    continue

                outputs = processor.process_event(upstream_event, converter)
                for output in outputs:
                    if output == _PROCESSING_COMMENT:
                        # 只有配置了 processing_delay_ms 时，stream_processor 才会产出该哨兵。
                        yield _PROCESSING_COMMENT
                        yield _PROCESSING_COMMENT
                        yield _PROCESSING_COMMENT
                        if processing_delay_ms and processing_delay_ms > 0:
                            # 注释先发给客户端，再执行固定延时，制造可感知的阶段过渡。
                            await asyncio.sleep(processing_delay_ms / 1000)
                        continue
                    yield output

            for chunk in processor.flush_pending():
                yield chunk

        return StreamingResponse(stream_generator(), media_type="text/event-stream", headers=_SSE_HEADERS)

    async def _aggregate_response_json(
        self,
        upstream_events: AsyncGenerator[UpstreamStreamItem, None],
        endpoint: str,
        custom_model_id: str,
        source_json: Optional[dict],
    ) -> dict:
        converter = self.parser_matcher.get_parser(custom_model_id)
        if endpoint == "chat/completions":
            return await assemble_chat_completion_response(
                upstream_events=upstream_events,
                converter=converter,
                custom_model_id=custom_model_id,
                source_json=source_json,
                logger=self.logger,
            )
        return await assemble_completion_response(
            upstream_events=upstream_events,
            converter=converter,
            custom_model_id=custom_model_id,
            source_json=source_json,
            logger=self.logger,
        )
