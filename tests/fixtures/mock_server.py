"""
Mock 后端服务器 - 用于回放录制的 LLM 响应

根据录制的 mock 数据，返回固定响应，验证 llm-proxy 组装逻辑。

支持两种模式：
1. ASGITransport 模式：直接挂载到 httpx.AsyncClient（推荐）
2. TestClient 模式：用于简单验证

使用示例:
    # ASGITransport 模式（推荐用于完整测试）
    mock_server = create_mock_server(mock_data)
    transport = mock_server.get_transport()
    async with httpx.AsyncClient(transport=transport) as client:
        response = await client.post(...)

    # TestClient 模式（用于简单验证）
    with mock_server.start() as client:
        response = client.post(...)
"""

import asyncio
import json
from typing import Optional
from fastapi import FastAPI, Request
from fastapi.responses import StreamingResponse, Response, JSONResponse
from starlette.testclient import TestClient
import httpx

from tests.helpers.request_signature import build_request_signature


class MockBackendServer:
    """模拟后端 API，根据录制数据返回固定响应"""

    def __init__(self, mock_data: dict, stream: bool = True):
        """
        Args:
            mock_data: 录制的后端响应数据
            stream: 是否使用流式返回
        """
        self.mock_data = mock_data
        self.stream = stream
        self.app = FastAPI()
        self._setup_routes()
        self._request_count = 0

    def _setup_routes(self):
        """设置路由"""

        @self.app.post("/v1/chat/completions")
        async def chat_completions(request: Request) -> Response:
            body = await request.json()
            self._request_count += 1

            # 根据请求内容查找匹配的响应
            response_data = self._find_matching_response(body)

            if response_data is None:
                return JSONResponse(
                    status_code=400,
                    content={"error": {"message": "No matching mock response found", "type": "invalid_request_error"}}
                )

            if self.stream:
                return self._stream_response(response_data)
            else:
                return self._non_stream_response(response_data)

        @self.app.get("/health")
        async def health():
            return {"status": "ok"}

    def _find_matching_response(self, request_body: dict) -> Optional[dict]:
        """
        根据请求匹配录制的响应

        匹配逻辑：
        1. 若录制样本带 request_signature，则优先按签名精确匹配
        2. 否则退回到基于 messages 历史中 tool_call 数量的轮次匹配
        """
        messages = request_body.get("messages", [])
        request_signature = build_request_signature(request_body)
        backend_responses = self.mock_data.get("backend_responses", [])

        for response in backend_responses:
            if response.get("request_signature") == request_signature:
                return response

        # 统计 messages 中有多少个 assistant 的 tool_call
        tool_call_count = 0
        for msg in messages:
            if msg.get("role") == "assistant" and msg.get("tool_calls"):
                tool_call_count += len(msg["tool_calls"])

        # 根据 tool_call 数量返回对应 step 的响应
        for resp in backend_responses:
            if resp.get("step") == tool_call_count + 1:
                return resp

        # 如果没有匹配的 step，返回最后一个（最终回复）
        return backend_responses[-1] if backend_responses else None

    def _stream_response(self, response_data: dict) -> StreamingResponse:
        """流式返回 SSE 响应"""
        chunks = response_data.get("raw_chunks", [])

        async def event_generator():
            for chunk in chunks:
                if chunk.startswith("data: "):
                    data_content = chunk[6:].strip()
                    if data_content == "[DONE]":
                        yield "data: [DONE]\n\n"
                    else:
                        # 模拟网络延迟
                        await asyncio.sleep(0.01)
                        yield chunk + "\n\n"
                else:
                    yield chunk + "\n\n"

        return StreamingResponse(
            event_generator(),
            media_type="text/event-stream",
            headers={"X-Mock-Backend": "true"}
        )

    def _non_stream_response(self, response_data: dict) -> Response:
        """
        非流式返回完整 JSON 响应

        从 raw_chunks 提取原始 content（保留 think 标签），构建非流式响应。
        让 llm-proxy 根据配置的 parser 自行处理 think 标签。
        """
        # 构建非流式响应
        response_id = f"chatcmpl-mock-{self._request_count}"
        created = 1700000000
        model = self.mock_data.get("workflow", {}).get("input", {}).get("model", "mock-model")

        # 从 raw_chunks 提取原始 content 和 reasoning
        content_parts = []
        reasoning_parts = []
        tool_calls_data = []
        finish_reason = "stop"

        chunks = response_data.get("raw_chunks", [])

        for chunk in chunks:
            if chunk.startswith("data: "):
                data_str = chunk[6:].strip()
                if data_str == "[DONE]":
                    continue
                try:
                    data = json.loads(data_str)
                    choices = data.get("choices", [])
                    if not choices:
                        continue

                    delta = choices[0].get("delta", {})
                    choice_finish = choices[0].get("finish_reason", "")

                    # 提取原始 content（保留 think 标签）
                    if "content" in delta and delta["content"]:
                        content_parts.append(delta["content"])

                    # 提取 reasoning 字段（如 OpenRouter/Gemini 格式）
                    if "reasoning" in delta and delta["reasoning"]:
                        reasoning_parts.append(delta["reasoning"])

                    # 提取 tool_calls
                    if "tool_calls" in delta:
                        for tc_delta in delta["tool_calls"]:
                            tool_calls_data.append(tc_delta)
                        if choice_finish == "tool_calls":
                            finish_reason = "tool_calls"

                except json.JSONDecodeError:
                    continue

        # 构建 message
        content = "".join(content_parts)
        reasoning = "".join(reasoning_parts)

        message = {
            "role": "assistant",
            "content": content
        }

        # 如果有 reasoning 字段（OpenRouter/Gemini 格式），添加到 message
        if reasoning:
            message["reasoning"] = reasoning

        # 处理 tool_calls（合并同一 index 的数据）
        if tool_calls_data:
            tool_calls_map = {}
            for tc_delta in tool_calls_data:
                idx = tc_delta.get("index", 0)
                if idx not in tool_calls_map:
                    tool_calls_map[idx] = {
                        "id": tc_delta.get("id", ""),
                        "type": "function",
                        "function": {"name": "", "arguments": ""}
                    }
                if tc_delta.get("id"):
                    tool_calls_map[idx]["id"] = tc_delta["id"]
                if tc_delta.get("function", {}).get("name"):
                    tool_calls_map[idx]["function"]["name"] = tc_delta["function"]["name"]
                if tc_delta.get("function", {}).get("arguments"):
                    tool_calls_map[idx]["function"]["arguments"] += tc_delta["function"]["arguments"]

            message["tool_calls"] = list(tool_calls_map.values())

        return JSONResponse(content={
            "id": response_id,
            "object": "chat.completion",
            "created": created,
            "model": model,
            "choices": [{
                "index": 0,
                "message": message,
                "finish_reason": finish_reason
            }]
        })

    def start(self) -> TestClient:
        """启动测试服务器（TestClient 模式）"""
        return TestClient(self.app, raise_server_exceptions=True)

    def get_transport(self) -> httpx.ASGITransport:
        """获取 ASGI Transport，用于挂载到 httpx.AsyncClient"""
        return httpx.ASGITransport(app=self.app)

    @property
    def base_url(self) -> str:
        """获取服务器基础 URL"""
        return "http://testserver"


def create_mock_server(mock_data: dict, stream: bool = True) -> MockBackendServer:
    """工厂函数：创建 Mock 后端服务器"""
    return MockBackendServer(mock_data, stream)
