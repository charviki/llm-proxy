import pytest
import json
import logging
from config.models import SSECoalescingConfig
from proxy.stream import StreamSimulator

test_logger = logging.getLogger("test_logger")

@pytest.mark.asyncio
async def test_simulate_chat_completion():
    response_json = {
        "id": "chatcmpl-test-123",
        "created": 1700000000,
        "choices": [
            {
                "message": {
                    "content": "Hello",
                    "reasoning_content": "thinking"
                }
            }
        ]
    }
    model_id = "test-model"

    generator = StreamSimulator.simulate_chat_completion(response_json, model_id, test_logger)
    chunks = [chunk async for chunk in generator]

    # Verify chunks
    # 1. role chunk
    assert b'"role":"assistant"' in chunks[0]
    # 2. reasoning chunk ("thinking") -> len 7 < 16，一次发送
    # 3. content chunk ("Hello") -> len 5 < 16，一次发送
    # 4. stop chunk
    # 5. [DONE]

    decoded_chunks = [c.decode('utf-8') for c in chunks]

    # Check reasoning chunks
    assert '"reasoning_content":"thinking"' in decoded_chunks[1]

    # Check content chunks
    assert '"content":"Hello"' in decoded_chunks[2]

    # Check finish reason
    assert '"finish_reason":"stop"' in decoded_chunks[3]
    assert "[DONE]" in decoded_chunks[4]

    # 验证复用原始响应的 id 和 created
    first_data = json.loads(decoded_chunks[0].replace("data: ", "").strip())
    assert first_data["id"] == "chatcmpl-test-123"
    assert first_data["created"] == 1700000000
    assert first_data["model"] == "test-model"

@pytest.mark.asyncio
async def test_simulate_completions():
    response_json = {
        "id": "cmpl-test-456",
        "created": 1700000000,
        "choices": [
            {
                "text": "Hello"
            }
        ]
    }
    model_id = "test-model"

    generator = StreamSimulator.simulate_completions(response_json, model_id, test_logger)
    chunks = [chunk async for chunk in generator]

    decoded_chunks = [c.decode('utf-8') for c in chunks]

    # 1. initial chunk
    assert '"text":""' in decoded_chunks[0]

    # 2. content chunk ("Hello") -> len 5 < 16，一次发送
    assert '"text":"Hello"' in decoded_chunks[1]

    # 3. stop chunk
    assert '"finish_reason":"stop"' in decoded_chunks[2]
    assert "[DONE]" in decoded_chunks[3]


# ===== 新增：tool_calls 流式模拟测试 =====

@pytest.mark.asyncio
async def test_simulate_chat_completion_with_tool_calls():
    """验证 tool_calls 被正确模拟为 SSE delta 块"""
    response_json = {
        "id": "chatcmpl-tool-test",
        "created": 1700000000,
        "choices": [{
            "message": {
                "content": "",
                "tool_calls": [
                    {
                        "id": "call_abc123",
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
    model_id = "test-model"

    generator = StreamSimulator.simulate_chat_completion(response_json, model_id, test_logger)
    chunks = [chunk async for chunk in generator]
    decoded_chunks = [c.decode('utf-8') for c in chunks]

    # 1. role chunk
    assert '"role":"assistant"' in decoded_chunks[0]

    # 2. tool_calls chunk(s) - 找到包含 tool_calls 的 chunk
    tool_call_chunks = [c for c in decoded_chunks if '"tool_calls"' in c]
    assert len(tool_call_chunks) >= 1, "Should have at least one tool_calls chunk"

    # 验证第一个 tool_call chunk 包含 function name
    first_tool_data = json.loads(tool_call_chunks[0].replace("data: ", "").strip())
    tc = first_tool_data["choices"][0]["delta"]["tool_calls"][0]
    assert tc["id"] == "call_abc123"
    assert tc["type"] == "function"
    assert tc["function"]["name"] == "read_file"

    # 3. finish_reason 应为 "tool_calls"
    finish_chunks = [c for c in decoded_chunks if '"finish_reason":"tool_calls"' in c]
    assert len(finish_chunks) == 1, "finish_reason should be 'tool_calls'"

    # 4. [DONE]
    assert "[DONE]" in decoded_chunks[-1]

@pytest.mark.asyncio
async def test_simulate_chat_completion_with_multiple_tool_calls():
    """验证多个 tool_calls 被正确模拟"""
    response_json = {
        "id": "chatcmpl-multi-tool",
        "created": 1700000000,
        "choices": [{
            "message": {
                "content": "",
                "tool_calls": [
                    {
                        "id": "call_001",
                        "type": "function",
                        "function": {
                            "name": "read_file",
                            "arguments": '{"path": "/a.txt"}'
                        }
                    },
                    {
                        "id": "call_002",
                        "type": "function",
                        "function": {
                            "name": "write_file",
                            "arguments": '{"path": "/b.txt", "content": "hello"}'
                        }
                    }
                ]
            }
        }]
    }

    generator = StreamSimulator.simulate_chat_completion(response_json, "test-model", test_logger)
    chunks = [chunk async for chunk in generator]
    decoded_chunks = [c.decode('utf-8') for c in chunks]

    # 验证两个 tool call 的 function name 都出现了
    all_text = "".join(decoded_chunks)
    assert '"name":"read_file"' in all_text
    assert '"name":"write_file"' in all_text
    assert '"id":"call_001"' in all_text
    assert '"id":"call_002"' in all_text

@pytest.mark.asyncio
async def test_simulate_chat_completion_with_long_arguments():
    """验证长 arguments 被分块发送"""
    long_args = json.dumps({"content": "x" * 100})  # 超过 20 字符
    response_json = {
        "id": "chatcmpl-long-args",
        "created": 1700000000,
        "choices": [{
            "message": {
                "content": "",
                "tool_calls": [{
                    "id": "call_long",
                    "type": "function",
                    "function": {
                        "name": "write_file",
                        "arguments": long_args
                    }
                }]
            }
        }]
    }

    generator = StreamSimulator.simulate_chat_completion(response_json, "test-model", test_logger)
    chunks = [chunk async for chunk in generator]
    decoded_chunks = [c.decode('utf-8') for c in chunks]

    # 收集所有 tool_calls chunk 中的 arguments
    collected_args = ""
    for c in decoded_chunks:
        if '"tool_calls"' in c:
            data_str = c.replace("data: ", "").strip()
            data = json.loads(data_str)
            delta = data["choices"][0]["delta"]
            if "tool_calls" in delta:
                collected_args += delta["tool_calls"][0]["function"]["arguments"]

    assert collected_args == long_args, "All argument chunks should reassemble to original"

@pytest.mark.asyncio
async def test_simulate_chat_completion_content_and_tool_calls():
    """验证同时有 content 和 tool_calls 时都能正确输出"""
    response_json = {
        "id": "chatcmpl-both",
        "created": 1700000000,
        "choices": [{
            "message": {
                "content": "Here is the result",
                "tool_calls": [{
                    "id": "call_mixed",
                    "type": "function",
                    "function": {
                        "name": "get_info",
                        "arguments": '{"key": "val"}'
                    }
                }]
            }
        }]
    }

    generator = StreamSimulator.simulate_chat_completion(response_json, "test-model", test_logger)
    chunks = [chunk async for chunk in generator]
    decoded_chunks = [c.decode('utf-8') for c in chunks]

    all_text = "".join(decoded_chunks)
    # content 和 tool_calls 都应出现
    assert '"content":' in all_text
    assert '"tool_calls"' in all_text
    assert '"finish_reason":"tool_calls"' in all_text

@pytest.mark.asyncio
async def test_sse_format_double_newlines():
    """验证所有 SSE 事件都以双换行结尾"""
    response_json = {
        "id": "chatcmpl-sse",
        "created": 1700000000,
        "choices": [{"message": {"content": "Hi"}}]
    }

    generator = StreamSimulator.simulate_chat_completion(response_json, "test-model", test_logger)
    chunks = [chunk async for chunk in generator]

    for chunk in chunks:
        decoded = chunk.decode('utf-8')
        assert decoded.endswith("\n\n"), f"SSE event should end with \\n\\n: {repr(decoded)}"


@pytest.mark.asyncio
async def test_simulate_chat_completion_uses_configurable_coalesced_content_chunks():
    response_json = {
        "id": "chatcmpl-coalesced-content",
        "created": 1700000000,
        "choices": [{
            "message": {
                "content": "abcdefghijklmnopqrst"
            }
        }]
    }

    generator = StreamSimulator.simulate_chat_completion(
        response_json,
        "test-model",
        test_logger,
        SSECoalescingConfig(enabled=True, window_ms=50, max_buffer_length=8),
    )
    chunks = [chunk async for chunk in generator]
    decoded_chunks = [chunk.decode("utf-8") for chunk in chunks]

    payloads = [
        json.loads(chunk.replace("data: ", "").strip())
        for chunk in decoded_chunks
        if chunk.startswith("data: {")
    ]
    content_chunks = [
        payload["choices"][0]["delta"]["content"]
        for payload in payloads
        if "content" in payload["choices"][0]["delta"]
    ]

    assert '"role":"assistant"' in decoded_chunks[0]
    assert content_chunks == ["abcdefgh", "ijklmnop", "qrst"]
    assert '"finish_reason":"stop"' in decoded_chunks[-2]
    assert decoded_chunks[-1] == "data: [DONE]\n\n"


@pytest.mark.asyncio
async def test_simulate_chat_completion_uses_coalesced_tool_call_arguments():
    arguments = '{"path":"/tmp/example.txt","mode":"read"}'
    response_json = {
        "id": "chatcmpl-coalesced-tool",
        "created": 1700000000,
        "choices": [{
            "message": {
                "content": "",
                "tool_calls": [{
                    "id": "call_semantic",
                    "type": "function",
                    "function": {
                        "name": "read_file",
                        "arguments": arguments
                    }
                }]
            }
        }]
    }

    generator = StreamSimulator.simulate_chat_completion(
        response_json,
        "test-model",
        test_logger,
        SSECoalescingConfig(enabled=True, window_ms=50, max_buffer_length=10),
    )
    chunks = [chunk async for chunk in generator]
    decoded_chunks = [chunk.decode("utf-8") for chunk in chunks]

    payloads = [
        json.loads(chunk.replace("data: ", "").strip())
        for chunk in decoded_chunks
        if chunk.startswith("data: {")
    ]
    tool_deltas = [
        payload["choices"][0]["delta"]["tool_calls"][0]
        for payload in payloads
        if "tool_calls" in payload["choices"][0]["delta"]
    ]

    assert len(tool_deltas) == 5
    assert "".join(delta["function"]["arguments"] for delta in tool_deltas) == arguments
    assert tool_deltas[0]["id"] == "call_semantic"
    assert tool_deltas[0]["type"] == "function"
    assert tool_deltas[0]["function"]["name"] == "read_file"
    assert "id" not in tool_deltas[1]
    assert "type" not in tool_deltas[1]
    assert "name" not in tool_deltas[1]["function"]
    assert '"finish_reason":"tool_calls"' in decoded_chunks[-2]
    assert decoded_chunks[-1] == "data: [DONE]\n\n"
