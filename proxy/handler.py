"""OpenAI 代理处理器 - 核心代理逻辑"""
import logging
import copy
import json
from typing import Optional
import httpx
from fastapi import Request
from fastapi.responses import JSONResponse, Response, StreamingResponse

from config.models import BackendsConfig
from .stream import StreamSimulator
from .converter import ChunkConverterMatcher
from .models import ModelsManager, Backend


class ProxyHandler:
    """OpenAI 代理处理器"""

    def __init__(self, backends: BackendsConfig, logger: logging.Logger, parser_matcher: ChunkConverterMatcher):
        self.backends = backends
        self.logger = logger
        self.parser_matcher = parser_matcher
        self.models_manager: Optional[ModelsManager] = None
        self._client: Optional[httpx.AsyncClient] = None

    async def set_client(self, client: httpx.AsyncClient) -> None:
        """设置 HTTP 客户端并加载模型列表"""
        self._client = client
        self.models_manager = ModelsManager(self.backends, self.logger)
        await self.models_manager.load_models(client)

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
                return JSONResponse(status_code=400, content={"error": f"JSON解析失败: {str(e)}"})

            self.logger.debug(f"[{endpoint}] 请求头: {dict(request.headers)}")
            self.logger.debug(f"[{endpoint}] 请求体: {json.dumps(req_json, ensure_ascii=False)}")

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
            self.logger.debug(f"[{endpoint}] 转发请求到: {target_url} (stream: {is_backend_stream})")

            if is_backend_stream:
                return await self._handle_stream_request(
                    req_json, headers, target_url, endpoint, custom_model_id
                )
            else:
                return await self._handle_non_stream_request(
                    req_json, headers, target_url, endpoint, custom_model_id, original_stream
                )

        except httpx.RequestError as e:
            self.logger.error(f"[{endpoint}] 请求异常: {str(e)}")
            return JSONResponse(status_code=503, content={"error": f"请求异常: {str(e)}"})
        except Exception as e:
            self.logger.error(f"[{endpoint}] 处理请求时发生错误: {str(e)}")
            return JSONResponse(status_code=500, content={"error": f"内部服务器错误: {str(e)}"})

    async def _handle_stream_request(
        self, req_json: dict, headers: dict, target_url: str,
        endpoint: str, custom_model_id: str
    ) -> StreamingResponse:
        """处理流式请求"""
        async def stream_generator():
            full_response_chunks = []
            converter = self.parser_matcher.get_parser(custom_model_id)
            try:
                async with self._client.stream("POST", target_url, json=req_json, headers=headers) as response:
                    if response.status_code != 200:
                        yield await response.aread()
                        return

                    # 优化：在循环外预先判断日志级别，避免每次迭代进行日志级别检查
                    is_debug = self.logger.isEnabledFor(logging.DEBUG)
                    async for line in response.aiter_lines():
                        # 仅在 DEBUG 模式下收集完整的响应流，减少生产环境下的内存消耗
                        if is_debug:
                            full_response_chunks.append(line.encode('utf-8') + b'\n')

                        if line.startswith("data: "):
                            processed = converter.parse(line[len("data: "):])
                            if processed is not None:
                                yield f"data: {processed}\n".encode('utf-8')
                        else:
                            yield f"{line}\n".encode('utf-8')

            except Exception as e:
                self.logger.error(f"[{endpoint}] 转发流式请求失败: {e}")
                yield f'data: {{"error": "转发流式请求失败: {str(e)}"}}\n\n'.encode('utf-8')
            finally:
                if full_response_chunks:
                    try:
                        full_response_bytes = b"".join(full_response_chunks)
                        full_response_str = full_response_bytes.decode('utf-8', errors='replace')
                        self.logger.debug(f"[{endpoint}] 完整流式响应内容:\n{full_response_str}")
                    except Exception as e:
                        self.logger.error(f"[{endpoint}] 记录完整流式响应失败: {e}")

        return StreamingResponse(stream_generator(), media_type="text/event-stream")

    async def _handle_non_stream_request(
        self, req_json: dict, headers: dict, target_url: str,
        endpoint: str, custom_model_id: str, original_stream: bool
    ) -> Response:
        """处理非流式请求"""
        response = await self._client.post(target_url, json=req_json, headers=headers)

        if response.status_code != 200:
            try:
                return JSONResponse(status_code=response.status_code, content=response.json())
            except:
                return Response(status_code=response.status_code, content=response.text)

        try:
            response_json = response.json()
        except Exception as e:
            self.logger.error(f"[{endpoint}] 解析响应JSON失败: {e}, 状态码: {response.status_code}, 响应文本: {response.text[:200]}")
            return JSONResponse(status_code=502, content={"error": f"上游服务返回了非JSON格式的响应，状态码: {response.status_code}, 响应: {response.text[:100]}..."})

        self.logger.debug(f"[{endpoint}] 响应体: {json.dumps(response_json, ensure_ascii=False)}")

        if original_stream and endpoint == "chat/completions":
            # 在非流式转流式前，利用对应的 Converter 进行一次全量的解析提取
            converter = self.parser_matcher.get_parser(custom_model_id)
            if converter is not None:
                message = response_json.get("choices", [{}])[0].get("message", {})
                if message:
                    # 使用 Converter 提取思考和正文
                    result = converter.process_chunk(message)
                    if result.reasoning:
                        message["reasoning_content"] = result.reasoning
                    if result.content is not None:
                        message["content"] = result.content
                    # 清理可能残留的私有字段
                    message.pop("reasoning", None)
                    message.pop("reasoning_details", None)

            return StreamingResponse(
                StreamSimulator.simulate_chat_completion(response_json, custom_model_id),
                media_type='text/event-stream'
            )
        elif original_stream and endpoint == "completions":
            return StreamingResponse(
                StreamSimulator.simulate_completions(response_json, custom_model_id),
                media_type='text/event-stream'
            )

        # 客户端请求的也是非流式，但我们仍然需要清理可能存在的标签或私有字段
        converter = self.parser_matcher.get_parser(custom_model_id)
        if converter is not None and endpoint == "chat/completions":
            message = response_json.get("choices", [{}])[0].get("message", {})
            if message:
                result = converter.process_chunk(message)
                if result.reasoning:
                    message["reasoning_content"] = result.reasoning
                if result.content is not None:
                    message["content"] = result.content
                message.pop("reasoning", None)
                message.pop("reasoning_details", None)

        if 'model' in response_json:
            response_json['model'] = custom_model_id

        return JSONResponse(content=response_json)
