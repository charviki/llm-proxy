import pytest
from unittest.mock import AsyncMock, MagicMock
from fastapi import Request
import json
import logging
from proxy.handler import ProxyHandler
from proxy.converter import ChunkConverterMatcher
from config.models import BackendsConfig, APIConfig, SSECoalescingConfig


class MockStreamResponse:
    def __init__(self, lines: list[str], status_code: int = 200, read_bytes: bytes = b""):
        self._lines = lines
        self.status_code = status_code
        self._read_bytes = read_bytes

    async def aiter_lines(self):
        for line in self._lines:
            yield line

    async def aread(self):
        return self._read_bytes


class MockStreamContext:
    def __init__(self, response: MockStreamResponse):
        self.response = response

    async def __aenter__(self):
        return self.response

    async def __aexit__(self, exc_type, exc, tb):
        return False


async def collect_streaming_response(response) -> bytes:
    chunks = []
    async for chunk in response.body_iterator:
        if isinstance(chunk, str):
            chunk = chunk.encode("utf-8")
        chunks.append(chunk)
    return b"".join(chunks)

@pytest.fixture
def mock_logger():
    return logging.getLogger("test_logger")

@pytest.fixture
def mock_parser_matcher(mock_logger):
    matcher = ChunkConverterMatcher({"default": "reasoning_content"}, mock_logger)
    return matcher

@pytest.fixture
def backends_config():
    return BackendsConfig(
        groups=[],
        apis=[
            APIConfig(
                name="Test API",
                endpoint="https://api.test.com",
                stream=True,
                custom_model_id="my-model",
                target_model_id="real-model"
            )
        ]
    )

@pytest.mark.asyncio
async def test_handle_completions_invalid_json(backends_config, mock_logger, mock_parser_matcher):
    handler = ProxyHandler(backends_config, mock_logger, mock_parser_matcher)

    request = AsyncMock(spec=Request)
    request.headers = {"Content-Type": "application/json"}
    request.json = AsyncMock(side_effect=json.JSONDecodeError("Expecting value", "", 0))

    response = await handler.handle_chat_completions(request)
    assert response.status_code == 400
    res_content = json.loads(response.body)
    assert "JSON解析失败" in res_content["error"]

@pytest.mark.asyncio
async def test_handle_chat_completions_model_not_found(backends_config, mock_logger, mock_parser_matcher):
    handler = ProxyHandler(backends_config, mock_logger, mock_parser_matcher)

    # Needs a mock client to initialize models_manager
    await handler.set_client(AsyncMock())

    request = AsyncMock(spec=Request)
    request.headers = {"Content-Type": "application/json"}
    request.json = AsyncMock(return_value={"model": "unknown-model"})

    response = await handler.handle_chat_completions(request)
    assert response.status_code == 400
    res_content = json.loads(response.body)
    assert "未找到匹配的模型" in res_content["error"]

@pytest.mark.asyncio
async def test_handle_chat_completions_non_stream(backends_config, mock_logger, mock_parser_matcher):
    handler = ProxyHandler(backends_config, mock_logger, mock_parser_matcher)

    mock_client = AsyncMock()
    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.json.return_value = {"id": "123", "choices": [{"message": {"content": "Hello"}}], "model": "real-model"}
    mock_client.post.return_value = mock_response

    await handler.set_client(mock_client)

    request = AsyncMock(spec=Request)
    request.headers = {"Content-Type": "application/json"}
    request.json = AsyncMock(return_value={"model": "my-model", "messages": []})

    response = await handler.handle_chat_completions(request)
    assert response.status_code == 200
    res_content = json.loads(response.body)
    assert res_content["model"] == "my-model"
    assert res_content["choices"][0]["message"]["content"] == "Hello"

@pytest.mark.asyncio
async def test_handle_chat_completions_stream_simulation(backends_config, mock_logger, mock_parser_matcher):
    # APIConfig says stream=False (not supported by backend)
    backends_config.apis[0].stream = False
    handler = ProxyHandler(backends_config, mock_logger, mock_parser_matcher)

    mock_client = AsyncMock()
    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.json.return_value = {
        "id": "123",
        "choices": [{"message": {"content": "Simulated"}}]
    }
    mock_client.post.return_value = mock_response

    await handler.set_client(mock_client)

    request = AsyncMock(spec=Request)
    request.headers = {"Content-Type": "application/json"}
    # Client requests stream=True
    request.json = AsyncMock(return_value={"model": "my-model", "messages": [], "stream": True})

    response = await handler.handle_chat_completions(request)

    # Backend was called with stream=False
    called_json = mock_client.post.call_args[1]["json"]
    assert called_json["stream"] is False

    # Response should be a StreamingResponse
    assert response.media_type == "text/event-stream"


@pytest.mark.asyncio
async def test_handle_chat_completions_native_stream_non_200_keeps_status_code(
    backends_config,
    mock_logger,
    mock_parser_matcher,
):
    handler = ProxyHandler(backends_config, mock_logger, mock_parser_matcher)

    mock_client = MagicMock()
    mock_client.stream.return_value = MockStreamContext(
        MockStreamResponse(
            lines=[],
            status_code=429,
            read_bytes=b'{"error":{"message":"rate limited"}}',
        )
    )

    await handler.set_client(mock_client)

    request = AsyncMock(spec=Request)
    request.headers = {"Content-Type": "application/json"}
    request.json = AsyncMock(return_value={"model": "my-model", "messages": [], "stream": True})

    response = await handler.handle_chat_completions(request)
    assert response.status_code == 429
    assert response.media_type == "application/json"
    res_content = json.loads(response.body)
    assert res_content["error"]["message"] == "rate limited"


@pytest.mark.asyncio
async def test_handle_completions_non_stream(backends_config, mock_logger, mock_parser_matcher):
    handler = ProxyHandler(backends_config, mock_logger, mock_parser_matcher)

    mock_client = AsyncMock()
    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.json.return_value = {
        "id": "cmpl-123",
        "model": "real-model",
        "choices": [{"text": "Hello world", "finish_reason": "stop"}],
    }
    mock_client.post.return_value = mock_response

    await handler.set_client(mock_client)

    request = AsyncMock(spec=Request)
    request.headers = {"Content-Type": "application/json"}
    request.json = AsyncMock(return_value={"model": "my-model", "prompt": "Say hi"})

    response = await handler.handle_completions(request)
    assert response.status_code == 200
    res_content = json.loads(response.body)
    assert res_content["model"] == "my-model"
    assert res_content["choices"][0]["text"] == "Hello world"
    assert res_content["choices"][0]["finish_reason"] == "stop"


@pytest.mark.asyncio
async def test_handle_completions_stream_simulation(backends_config, mock_logger, mock_parser_matcher):
    backends_config.apis[0].stream = False
    handler = ProxyHandler(backends_config, mock_logger, mock_parser_matcher)

    mock_client = AsyncMock()
    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.json.return_value = {
        "id": "cmpl-456",
        "created": 1700000000,
        "model": "real-model",
        "choices": [{"text": "Hello world", "finish_reason": "stop"}],
    }
    mock_client.post.return_value = mock_response

    await handler.set_client(mock_client)

    request = AsyncMock(spec=Request)
    request.headers = {"Content-Type": "application/json"}
    request.json = AsyncMock(return_value={"model": "my-model", "prompt": "Say hi", "stream": True})

    response = await handler.handle_completions(request)
    body = (await collect_streaming_response(response)).decode("utf-8")

    assert response.media_type == "text/event-stream"
    assert '"model":"my-model"' in body
    assert '"text":"Hello world"' in body
    assert '"finish_reason":"stop"' in body
    assert body.rstrip().endswith("data: [DONE]")


# ===== 新增：tool_calls 相关测试 =====

@pytest.mark.asyncio
async def test_handle_chat_completions_non_stream_with_tool_calls(backends_config, mock_logger, mock_parser_matcher):
    """非流式响应中 tool_calls 应被正确保留"""
    handler = ProxyHandler(backends_config, mock_logger, mock_parser_matcher)

    mock_client = AsyncMock()
    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.json.return_value = {
        "id": "chatcmpl-tool-test",
        "model": "real-model",
        "choices": [{
            "message": {
                "content": "",
                "tool_calls": [
                    {
                        "id": "call_abc",
                        "type": "function",
                        "function": {
                            "name": "read_file",
                            "arguments": '{"path": "/tmp/test.txt"}'
                        }
                    }
                ]
            }
        }]
    }
    mock_client.post.return_value = mock_response

    await handler.set_client(mock_client)

    request = AsyncMock(spec=Request)
    request.headers = {"Content-Type": "application/json"}
    request.json = AsyncMock(return_value={"model": "my-model", "messages": []})

    response = await handler.handle_chat_completions(request)
    assert response.status_code == 200
    res_content = json.loads(response.body)

    # tool_calls 应保留
    assert "tool_calls" in res_content["choices"][0]["message"]
    tc = res_content["choices"][0]["message"]["tool_calls"][0]
    assert tc["id"] == "call_abc"
    assert tc["function"]["name"] == "read_file"

    # model ID 应被回映射
    assert res_content["model"] == "my-model"


@pytest.mark.asyncio
async def test_handle_chat_completions_non_stream_aggregates_reasoning_content(backends_config, mock_logger):
    parser_matcher = ChunkConverterMatcher({"reasoning": ["my-model"], "default": "reasoning_content"}, mock_logger)
    handler = ProxyHandler(backends_config, mock_logger, parser_matcher)

    mock_client = AsyncMock()
    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.json.return_value = {
        "id": "chatcmpl-reasoning",
        "model": "real-model",
        "choices": [{
            "message": {
                "reasoning": "thinking",
                "content": "answer",
            },
            "finish_reason": "stop",
        }]
    }
    mock_client.post.return_value = mock_response

    await handler.set_client(mock_client)

    request = AsyncMock(spec=Request)
    request.headers = {"Content-Type": "application/json"}
    request.json = AsyncMock(return_value={"model": "my-model", "messages": []})

    response = await handler.handle_chat_completions(request)
    res_content = json.loads(response.body)

    assert res_content["model"] == "my-model"
    assert res_content["choices"][0]["message"]["content"] == "answer"
    assert res_content["choices"][0]["message"]["reasoning_content"] == "thinking"
    assert "reasoning" not in res_content["choices"][0]["message"]

@pytest.mark.asyncio
async def test_handle_chat_completions_stream_simulation_with_tool_calls(backends_config, mock_logger, mock_parser_matcher):
    """非流式转流式时 tool_calls 应被正确模拟"""
    backends_config.apis[0].stream = False
    handler = ProxyHandler(backends_config, mock_logger, mock_parser_matcher)

    mock_client = AsyncMock()
    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.json.return_value = {
        "id": "chatcmpl-tool-stream",
        "created": 1700000000,
        "model": "real-model",
        "choices": [{
            "message": {
                "content": "",
                "tool_calls": [
                    {
                        "id": "call_xyz",
                        "type": "function",
                        "function": {
                            "name": "execute_command",
                            "arguments": '{"cmd": "ls -la"}'
                        }
                    }
                ]
            }
        }]
    }
    mock_client.post.return_value = mock_response

    await handler.set_client(mock_client)

    request = AsyncMock(spec=Request)
    request.headers = {"Content-Type": "application/json"}
    request.json = AsyncMock(return_value={"model": "my-model", "messages": [], "stream": True})

    response = await handler.handle_chat_completions(request)

    # Response should be a StreamingResponse
    assert response.media_type == "text/event-stream"

    # 验证 SSE 响应头
    assert response.headers.get("Cache-Control") == "no-cache"
    assert response.headers.get("X-Accel-Buffering") == "no"

@pytest.mark.asyncio
async def test_handle_chat_completions_sse_headers(backends_config, mock_logger, mock_parser_matcher):
    """StreamingResponse 应包含标准 SSE 响应头"""
    backends_config.apis[0].stream = False
    handler = ProxyHandler(backends_config, mock_logger, mock_parser_matcher)

    mock_client = AsyncMock()
    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.json.return_value = {
        "id": "123",
        "choices": [{"message": {"content": "test"}}]
    }
    mock_client.post.return_value = mock_response

    await handler.set_client(mock_client)

    request = AsyncMock(spec=Request)
    request.headers = {"Content-Type": "application/json"}
    request.json = AsyncMock(return_value={"model": "my-model", "messages": [], "stream": True})

    response = await handler.handle_chat_completions(request)

    assert response.headers.get("Cache-Control") == "no-cache"
    assert response.headers.get("X-Accel-Buffering") == "no"
    assert response.headers.get("Connection") == "keep-alive"


@pytest.mark.asyncio
async def test_handle_chat_completions_native_stream_coalesces_after_converter_parse(
    backends_config,
    mock_logger,
    mock_parser_matcher,
):
    handler = ProxyHandler(
        backends_config,
        mock_logger,
        mock_parser_matcher,
        SSECoalescingConfig(enabled=True, window_ms=50, max_buffer_length=128),
    )

    mock_client = MagicMock()
    mock_client.stream.return_value = MockStreamContext(
        MockStreamResponse([
            'data: {"id":"1","model":"real-model","choices":[{"delta":{"content":"Hel"},"finish_reason":null}]}',
            "",
            'data: {"id":"1","model":"real-model","choices":[{"delta":{"content":"lo"},"finish_reason":null}]}',
            "",
            'data: {"id":"1","model":"real-model","choices":[{"delta":{},"finish_reason":"stop"}]}',
            "",
            "data: [DONE]",
            "",
        ])
    )

    await handler.set_client(mock_client)

    request = AsyncMock(spec=Request)
    request.headers = {"Content-Type": "application/json"}
    request.json = AsyncMock(return_value={"model": "my-model", "messages": [], "stream": True})

    response = await handler.handle_chat_completions(request)
    body = (await collect_streaming_response(response)).decode("utf-8")

    assert '"model":"my-model"' in body
    assert '"content":"Hello"' in body
    assert '"content":"Hel"' not in body
    assert '"content":"lo"' not in body
    assert '"finish_reason":"stop"' in body
    assert body.rstrip().endswith("data: [DONE]")


@pytest.mark.asyncio
async def test_handle_chat_completions_native_stream_coalesces_reasoning_content_with_role(
    backends_config,
    mock_logger,
):
    parser_matcher = ChunkConverterMatcher({"reasoning": ["my-model"]}, mock_logger)
    handler = ProxyHandler(
        backends_config,
        mock_logger,
        parser_matcher,
        SSECoalescingConfig(enabled=True, window_ms=50, max_buffer_length=128),
    )

    mock_client = MagicMock()
    mock_client.stream.return_value = MockStreamContext(
        MockStreamResponse([
            'data: {"id":"1","model":"real-model","choices":[{"delta":{"role":"assistant","reasoning":"你","reasoning_details":[{"type":"reasoning.text","text":"你"}]},"finish_reason":null}]}',
            "",
            'data: {"id":"1","model":"real-model","choices":[{"delta":{"role":"assistant","reasoning":"好","reasoning_details":[{"type":"reasoning.text","text":"好"}]},"finish_reason":null}]}',
            "",
            'data: {"id":"1","model":"real-model","choices":[{"delta":{"content":"世界","role":"assistant"},"finish_reason":null}]}',
            "",
            'data: {"id":"1","model":"real-model","choices":[{"delta":{},"finish_reason":"stop"}]}',
            "",
            "data: [DONE]",
            "",
        ])
    )

    await handler.set_client(mock_client)

    request = AsyncMock(spec=Request)
    request.headers = {"Content-Type": "application/json"}
    request.json = AsyncMock(return_value={"model": "my-model", "messages": [], "stream": True})

    response = await handler.handle_chat_completions(request)
    body = (await collect_streaming_response(response)).decode("utf-8")
    payloads = [
        json.loads(line[len("data: "):])
        for line in body.splitlines()
        if line.startswith("data: {")
    ]

    reasoning_deltas = [
        payload["choices"][0]["delta"]
        for payload in payloads
        if "reasoning_content" in payload["choices"][0]["delta"]
    ]
    content_deltas = [
        payload["choices"][0]["delta"]
        for payload in payloads
        if "content" in payload["choices"][0]["delta"]
    ]

    assert reasoning_deltas == [{"role": "assistant", "reasoning_content": "你好"}]
    assert content_deltas == [{"role": "assistant", "content": "世界"}]
    assert '"finish_reason":"stop"' in body
    assert body.rstrip().endswith("data: [DONE]")


@pytest.mark.asyncio
async def test_handle_chat_completions_native_stream_coalesces_tool_call_arguments(
    backends_config,
    mock_logger,
    mock_parser_matcher,
):
    handler = ProxyHandler(
        backends_config,
        mock_logger,
        mock_parser_matcher,
        SSECoalescingConfig(enabled=True, window_ms=50, max_buffer_length=128),
    )

    mock_client = MagicMock()
    mock_client.stream.return_value = MockStreamContext(
        MockStreamResponse([
            'data: {"id":"1","model":"real-model","choices":[{"delta":{"tool_calls":[{"index":0,"id":"call_1","type":"function","function":{"name":"read_file","arguments":"{\\"path\\":"}}]},"finish_reason":null}]}',
            "",
            'data: {"id":"1","model":"real-model","choices":[{"delta":{"tool_calls":[{"index":0,"function":{"arguments":"\\"/tmp/a.txt\\"}"}}]},"finish_reason":null}]}',
            "",
            'data: {"id":"1","model":"real-model","choices":[{"delta":{},"finish_reason":"tool_calls"}]}',
            "",
            "data: [DONE]",
            "",
        ])
    )

    await handler.set_client(mock_client)

    request = AsyncMock(spec=Request)
    request.headers = {"Content-Type": "application/json"}
    request.json = AsyncMock(return_value={"model": "my-model", "messages": [], "stream": True})

    response = await handler.handle_chat_completions(request)
    body = (await collect_streaming_response(response)).decode("utf-8")

    payloads = [
        json.loads(line[len("data: "):])
        for line in body.splitlines()
        if line.startswith("data: {")
    ]
    tool_deltas = [
        payload["choices"][0]["delta"]["tool_calls"][0]
        for payload in payloads
        if "tool_calls" in payload["choices"][0]["delta"]
    ]

    assert len(tool_deltas) == 1
    assert tool_deltas[0]["id"] == "call_1"
    assert tool_deltas[0]["type"] == "function"
    assert tool_deltas[0]["function"]["name"] == "read_file"
    assert tool_deltas[0]["function"]["arguments"] == '{"path":"/tmp/a.txt"}'
    assert '"finish_reason":"tool_calls"' in body
    assert body.rstrip().endswith("data: [DONE]")


@pytest.mark.asyncio
async def test_handle_chat_completions_native_stream_coalesces_tool_call_arguments_with_role(
    backends_config,
    mock_logger,
    mock_parser_matcher,
):
    handler = ProxyHandler(
        backends_config,
        mock_logger,
        mock_parser_matcher,
        SSECoalescingConfig(enabled=True, window_ms=50, max_buffer_length=128),
    )

    mock_client = MagicMock()
    mock_client.stream.return_value = MockStreamContext(
        MockStreamResponse([
            'data: {"id":"1","model":"real-model","choices":[{"delta":{"role":"assistant","tool_calls":[{"index":0,"id":"call_1","type":"function","function":{"name":"Glob","arguments":"{\\"pattern\\":"}}]},"finish_reason":null}]}',
            "",
            'data: {"id":"1","model":"real-model","choices":[{"delta":{"role":"assistant","tool_calls":[{"index":0,"function":{"arguments":"\\"*\\"}"}}]},"finish_reason":null}]}',
            "",
            'data: {"id":"1","model":"real-model","choices":[{"delta":{"role":"assistant"},"finish_reason":"tool_calls"}]}',
            "",
            "data: [DONE]",
            "",
        ])
    )

    await handler.set_client(mock_client)

    request = AsyncMock(spec=Request)
    request.headers = {"Content-Type": "application/json"}
    request.json = AsyncMock(return_value={"model": "my-model", "messages": [], "stream": True})

    response = await handler.handle_chat_completions(request)
    body = (await collect_streaming_response(response)).decode("utf-8")

    payloads = [
        json.loads(line[len("data: "):])
        for line in body.splitlines()
        if line.startswith("data: {")
    ]
    tool_deltas = [
        payload["choices"][0]["delta"]
        for payload in payloads
        if "tool_calls" in payload["choices"][0]["delta"]
    ]

    assert tool_deltas == [{
        "role": "assistant",
        "tool_calls": [{
            "index": 0,
            "id": "call_1",
            "type": "function",
            "function": {
                "name": "Glob",
                "arguments": '{"pattern":"*"}',
            },
        }],
    }]
    assert '"finish_reason":"tool_calls"' in body
    assert body.rstrip().endswith("data: [DONE]")


@pytest.mark.asyncio
async def test_handle_chat_completions_native_stream_flushes_before_error_event(
    backends_config,
    mock_logger,
    mock_parser_matcher,
):
    handler = ProxyHandler(
        backends_config,
        mock_logger,
        mock_parser_matcher,
        SSECoalescingConfig(enabled=True, window_ms=50, max_buffer_length=128),
    )

    mock_client = MagicMock()
    mock_client.stream.return_value = MockStreamContext(
        MockStreamResponse([
            'data: {"id":"1","model":"real-model","choices":[{"delta":{"content":"buffered"},"finish_reason":null}]}',
            "",
            "event: error",
            'data: {"message":"backend failed"}',
            "",
        ])
    )

    await handler.set_client(mock_client)

    request = AsyncMock(spec=Request)
    request.headers = {"Content-Type": "application/json"}
    request.json = AsyncMock(return_value={"model": "my-model", "messages": [], "stream": True})

    response = await handler.handle_chat_completions(request)
    body = (await collect_streaming_response(response)).decode("utf-8")

    assert body.index('"content":"buffered"') < body.index("event: error")
    assert 'event: error\ndata: {"message":"backend failed"}\n\n' in body


@pytest.mark.asyncio
async def test_handle_chat_completions_native_stream_passthroughs_non_data_event(
    backends_config,
    mock_logger,
    mock_parser_matcher,
):
    handler = ProxyHandler(
        backends_config,
        mock_logger,
        mock_parser_matcher,
        SSECoalescingConfig(enabled=True, window_ms=50, max_buffer_length=128),
    )

    mock_client = MagicMock()
    mock_client.stream.return_value = MockStreamContext(
        MockStreamResponse([
            "event: ping",
            "id: 7",
            "",
            'data: {"id":"1","model":"real-model","choices":[{"delta":{"content":"ok"},"finish_reason":null}]}',
            "",
            'data: {"id":"1","model":"real-model","choices":[{"delta":{},"finish_reason":"stop"}]}',
            "",
            "data: [DONE]",
            "",
        ])
    )

    await handler.set_client(mock_client)

    request = AsyncMock(spec=Request)
    request.headers = {"Content-Type": "application/json"}
    request.json = AsyncMock(return_value={"model": "my-model", "messages": [], "stream": True})

    response = await handler.handle_chat_completions(request)
    body = (await collect_streaming_response(response)).decode("utf-8")

    assert "event: ping\nid: 7\n\n" in body
    assert '"content":"ok"' in body


@pytest.mark.asyncio
async def test_handle_chat_completions_native_stream_supports_multiline_data_event(
    backends_config,
    mock_logger,
    mock_parser_matcher,
):
    handler = ProxyHandler(backends_config, mock_logger, mock_parser_matcher)

    mock_client = MagicMock()
    mock_client.stream.return_value = MockStreamContext(
        MockStreamResponse([
            'data: {"id":"1","model":"real-model",',
            'data: "choices":[{"delta":{"content":"hello"},"finish_reason":null}]}',
            "",
            'data: {"id":"1","model":"real-model","choices":[{"delta":{},"finish_reason":"stop"}]}',
            "",
            "data: [DONE]",
            "",
        ])
    )

    await handler.set_client(mock_client)

    request = AsyncMock(spec=Request)
    request.headers = {"Content-Type": "application/json"}
    request.json = AsyncMock(return_value={"model": "my-model", "messages": [], "stream": True})

    response = await handler.handle_chat_completions(request)
    body = (await collect_streaming_response(response)).decode("utf-8")

    assert '"content":"hello"' in body
    assert '"finish_reason":"stop"' in body


@pytest.mark.asyncio
async def test_handle_chat_completions_native_stream_keeps_chunked_output_when_disabled(
    backends_config,
    mock_logger,
    mock_parser_matcher,
):
    handler = ProxyHandler(
        backends_config,
        mock_logger,
        mock_parser_matcher,
        SSECoalescingConfig(enabled=False, window_ms=50, max_buffer_length=128),
    )

    mock_client = MagicMock()
    mock_client.stream.return_value = MockStreamContext(
        MockStreamResponse([
            'data: {"id":"1","model":"real-model","choices":[{"delta":{"content":"Hel"},"finish_reason":null}]}',
            "",
            'data: {"id":"1","model":"real-model","choices":[{"delta":{"content":"lo"},"finish_reason":null}]}',
            "",
            'data: {"id":"1","model":"real-model","choices":[{"delta":{},"finish_reason":"stop"}]}',
            "",
            "data: [DONE]",
            "",
        ])
    )

    await handler.set_client(mock_client)

    request = AsyncMock(spec=Request)
    request.headers = {"Content-Type": "application/json"}
    request.json = AsyncMock(return_value={"model": "my-model", "messages": [], "stream": True})

    response = await handler.handle_chat_completions(request)
    body = (await collect_streaming_response(response)).decode("utf-8")

    assert '"content":"Hel"' in body
    assert '"content":"lo"' in body
    assert '"content":"Hello"' not in body


@pytest.mark.asyncio
async def test_handle_chat_completions_stream_simulation_reuses_coalesced_output(
    backends_config,
    mock_logger,
    mock_parser_matcher,
):
    backends_config.apis[0].stream = False
    handler = ProxyHandler(
        backends_config,
        mock_logger,
        mock_parser_matcher,
        SSECoalescingConfig(enabled=True, window_ms=50, max_buffer_length=8),
    )

    mock_client = AsyncMock()
    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.json.return_value = {
        "id": "chatcmpl-sim-coalesced",
        "created": 1700000000,
        "choices": [{
            "message": {
                "content": "abcdefghijklmnopqrst"
            }
        }],
    }
    mock_client.post.return_value = mock_response

    await handler.set_client(mock_client)

    request = AsyncMock(spec=Request)
    request.headers = {"Content-Type": "application/json"}
    request.json = AsyncMock(return_value={"model": "my-model", "messages": [], "stream": True})

    response = await handler.handle_chat_completions(request)
    body = (await collect_streaming_response(response)).decode("utf-8")

    assert '"role":"assistant"' in body
    assert '"content":"abcdefgh"' in body
    assert '"content":"ijklmnop"' in body
    assert '"content":"qrst"' in body
    assert '"finish_reason":"stop"' in body
    assert body.rstrip().endswith("data: [DONE]")
