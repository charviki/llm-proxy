from config.models import SSECoalescingConfig
from proxy.sse_coalescer import SSESemanticCoalescer


def _content_chunk(content: str, finish_reason=None) -> dict:
    return {
        "id": "chatcmpl-test",
        "object": "chat.completion.chunk",
        "created": 1700000000,
        "model": "test-model",
        "choices": [{
            "index": 0,
            "delta": {"content": content},
            "finish_reason": finish_reason,
        }],
    }


def _tool_chunk(index: int, arguments: str, *, tool_call_id: str = "", function_name: str = "", tool_call_type: str = "function", finish_reason=None) -> dict:
    tool_call = {
        "index": index,
        "function": {
            "arguments": arguments,
        },
    }
    if tool_call_id:
        tool_call["id"] = tool_call_id
    if tool_call_type:
        tool_call["type"] = tool_call_type
    if function_name:
        tool_call["function"]["name"] = function_name

    return {
        "id": "chatcmpl-test",
        "object": "chat.completion.chunk",
        "created": 1700000000,
        "model": "test-model",
        "choices": [{
            "index": 0,
            "delta": {"tool_calls": [tool_call]},
            "finish_reason": finish_reason,
        }],
    }


def _collect_semantic_output(chunks: list[dict]) -> dict:
    content = []
    reasoning_content = []
    tool_arguments = []
    finish_reasons = []

    for chunk in chunks:
        choice = chunk["choices"][0]
        delta = choice["delta"]
        if delta.get("content"):
            content.append(delta["content"])
        if delta.get("reasoning_content"):
            reasoning_content.append(delta["reasoning_content"])
        for tool_call in delta.get("tool_calls", []):
            tool_arguments.append(tool_call["function"].get("arguments", ""))
        if choice.get("finish_reason"):
            finish_reasons.append(choice["finish_reason"])

    return {
        "content": "".join(content),
        "reasoning_content": "".join(reasoning_content),
        "tool_arguments": "".join(tool_arguments),
        "finish_reasons": finish_reasons,
    }


def test_coalescer_merges_content_until_forced_flush():
    coalescer = SSESemanticCoalescer(
        SSECoalescingConfig(enabled=True, window_ms=20, max_buffer_length=32)
    )

    assert coalescer.push_chunk(_content_chunk("Hel"), now_ms=0) == []
    assert coalescer.push_chunk(_content_chunk("lo"), now_ms=10) == []

    flushed = coalescer.flush_pending()
    assert len(flushed) == 1
    assert flushed[0]["choices"][0]["delta"] == {"content": "Hello"}


def test_coalescer_flushes_content_before_finish_reason():
    coalescer = SSESemanticCoalescer(
        SSECoalescingConfig(enabled=True, window_ms=20, max_buffer_length=32)
    )

    assert coalescer.push_chunk(_content_chunk("Hello"), now_ms=0) == []

    finish_chunk = {
        "id": "chatcmpl-test",
        "object": "chat.completion.chunk",
        "created": 1700000000,
        "model": "test-model",
        "choices": [{
            "index": 0,
            "delta": {},
            "finish_reason": "stop",
        }],
    }
    outputs = coalescer.push_chunk(finish_chunk, now_ms=5)

    assert len(outputs) == 2
    assert outputs[0]["choices"][0]["delta"] == {"content": "Hello"}
    assert outputs[1]["choices"][0]["finish_reason"] == "stop"


def test_coalescer_uses_tool_first_chunk_and_continuation_rules():
    coalescer = SSESemanticCoalescer(
        SSECoalescingConfig(enabled=True, window_ms=20, max_buffer_length=32)
    )

    assert coalescer.push_chunk(
        _tool_chunk(0, '{"a":', tool_call_id="call_1", function_name="write_file"),
        now_ms=0,
    ) == []

    first_flush = coalescer.flush_expired(now_ms=20)
    assert len(first_flush) == 1
    first_tool = first_flush[0]["choices"][0]["delta"]["tool_calls"][0]
    assert first_tool["id"] == "call_1"
    assert first_tool["type"] == "function"
    assert first_tool["function"]["name"] == "write_file"
    assert first_tool["function"]["arguments"] == '{"a":'

    assert coalescer.push_chunk(_tool_chunk(0, '"b"}'), now_ms=25) == []
    second_flush = coalescer.flush_pending()
    assert len(second_flush) == 1
    second_tool = second_flush[0]["choices"][0]["delta"]["tool_calls"][0]
    assert second_tool["index"] == 0
    assert second_tool["function"]["arguments"] == '"b"}'
    assert "id" not in second_tool
    assert "type" not in second_tool
    assert "name" not in second_tool["function"]


def test_coalescer_flushes_on_semantic_boundary_switch():
    coalescer = SSESemanticCoalescer(
        SSECoalescingConfig(enabled=True, window_ms=20, max_buffer_length=32)
    )

    assert coalescer.push_chunk(_content_chunk("abc"), now_ms=0) == []

    outputs = coalescer.push_chunk(
        _tool_chunk(0, '{"path":"a"}', tool_call_id="call_2", function_name="read_file"),
        now_ms=5,
    )
    assert len(outputs) == 1
    assert outputs[0]["choices"][0]["delta"] == {"content": "abc"}

    pending = coalescer.flush_pending()
    assert len(pending) == 1
    tool_call = pending[0]["choices"][0]["delta"]["tool_calls"][0]
    assert tool_call["id"] == "call_2"
    assert tool_call["function"]["name"] == "read_file"


def test_coalescer_flushes_when_tool_call_target_changes_or_threshold_reached():
    coalescer = SSESemanticCoalescer(
        SSECoalescingConfig(enabled=True, window_ms=20, max_buffer_length=5)
    )

    outputs = coalescer.push_chunk(
        _tool_chunk(0, '{"abc', tool_call_id="call_3", function_name="read_file"),
        now_ms=0,
    )
    assert len(outputs) == 1
    first_tool = outputs[0]["choices"][0]["delta"]["tool_calls"][0]
    assert first_tool["function"]["arguments"] == '{"abc'

    assert coalescer.push_chunk(_tool_chunk(0, '"}'), now_ms=1) == []

    switched = coalescer.push_chunk(
        _tool_chunk(1, '{"x":1}', tool_call_id="call_4", function_name="write_file"),
        now_ms=2,
    )
    assert len(switched) == 2
    assert switched[0]["choices"][0]["delta"]["tool_calls"][0]["function"]["arguments"] == '"}'
    assert switched[1]["choices"][0]["delta"]["tool_calls"][0]["function"]["arguments"] == '{"x":1}'


def test_coalescer_merges_tool_calls_and_preserves_role():
    coalescer = SSESemanticCoalescer(
        SSECoalescingConfig(enabled=True, window_ms=20, max_buffer_length=32)
    )

    chunk_1 = {
        "id": "chatcmpl-test",
        "object": "chat.completion.chunk",
        "created": 1700000000,
        "model": "test-model",
        "choices": [{
            "index": 0,
            "delta": {
                "role": "assistant",
                "tool_calls": [{
                    "index": 0,
                    "id": "call_5",
                    "type": "function",
                    "function": {"name": "Glob", "arguments": "{\"pattern\":"},
                }],
            },
            "finish_reason": None,
        }],
    }
    chunk_2 = {
        "id": "chatcmpl-test",
        "object": "chat.completion.chunk",
        "created": 1700000000,
        "model": "test-model",
        "choices": [{
            "index": 0,
            "delta": {
                "role": "assistant",
                "tool_calls": [{
                    "index": 0,
                    "function": {"arguments": "\"*\"}"},
                }],
            },
            "finish_reason": None,
        }],
    }

    assert coalescer.push_chunk(chunk_1, now_ms=0) == []
    assert coalescer.push_chunk(chunk_2, now_ms=10) == []

    flushed = coalescer.flush_pending()
    assert len(flushed) == 1
    tool_call = flushed[0]["choices"][0]["delta"]["tool_calls"][0]
    assert flushed[0]["choices"][0]["delta"]["role"] == "assistant"
    assert tool_call["id"] == "call_5"
    assert tool_call["type"] == "function"
    assert tool_call["function"]["name"] == "Glob"
    assert tool_call["function"]["arguments"] == '{"pattern":"*"}'


def test_coalescer_passthrough_when_disabled():
    chunk = _content_chunk("Hello")
    coalescer = SSESemanticCoalescer(
        SSECoalescingConfig(enabled=False, window_ms=20, max_buffer_length=32)
    )

    outputs = coalescer.push_chunk(chunk, now_ms=0)
    assert outputs == [chunk]


def test_coalescer_merges_reasoning_content_and_preserves_role():
    coalescer = SSESemanticCoalescer(
        SSECoalescingConfig(enabled=True, window_ms=20, max_buffer_length=32)
    )

    chunk_1 = {
        "id": "chatcmpl-test",
        "object": "chat.completion.chunk",
        "created": 1700000000,
        "model": "test-model",
        "choices": [{
            "index": 0,
            "delta": {"role": "assistant", "reasoning_content": "你"},
            "finish_reason": None,
        }],
    }
    chunk_2 = {
        "id": "chatcmpl-test",
        "object": "chat.completion.chunk",
        "created": 1700000000,
        "model": "test-model",
        "choices": [{
            "index": 0,
            "delta": {"role": "assistant", "reasoning_content": "好"},
            "finish_reason": None,
        }],
    }

    assert coalescer.push_chunk(chunk_1, now_ms=0) == []
    assert coalescer.push_chunk(chunk_2, now_ms=10) == []

    flushed = coalescer.flush_pending()
    assert len(flushed) == 1
    assert flushed[0]["choices"][0]["delta"] == {
        "role": "assistant",
        "reasoning_content": "你好",
    }


def test_coalescer_reconstructs_final_semantic_output():
    coalescer = SSESemanticCoalescer(
        SSECoalescingConfig(enabled=True, window_ms=20, max_buffer_length=64)
    )

    assert coalescer.push_chunk({
        "id": "chatcmpl-test",
        "object": "chat.completion.chunk",
        "created": 1700000000,
        "model": "test-model",
        "choices": [{
            "index": 0,
            "delta": {"role": "assistant", "reasoning_content": "先分析"},
            "finish_reason": None,
        }],
    }, now_ms=0) == []
    outputs = coalescer.push_chunk(_content_chunk("结"), now_ms=10)
    assert outputs != []
    assert coalescer.push_chunk(_content_chunk("论"), now_ms=15) == []

    outputs.extend(coalescer.push_chunk(
        _tool_chunk(0, '{"path":"a.txt"}', tool_call_id="call_x", function_name="read_file", finish_reason="tool_calls"),
        now_ms=25,
    ))

    semantic_output = _collect_semantic_output(outputs + coalescer.flush_pending())

    assert semantic_output["reasoning_content"] == "先分析"
    assert semantic_output["content"] == "结论"
    assert semantic_output["tool_arguments"] == '{"path":"a.txt"}'
    assert semantic_output["finish_reasons"] == ["tool_calls"]


def test_coalescer_flushes_when_text_field_changes():
    coalescer = SSESemanticCoalescer(
        SSECoalescingConfig(enabled=True, window_ms=20, max_buffer_length=32)
    )

    reasoning_chunk = {
        "id": "chatcmpl-test",
        "object": "chat.completion.chunk",
        "created": 1700000000,
        "model": "test-model",
        "choices": [{
            "index": 0,
            "delta": {"role": "assistant", "reasoning_content": "思考"},
            "finish_reason": None,
        }],
    }
    content_chunk = {
        "id": "chatcmpl-test",
        "object": "chat.completion.chunk",
        "created": 1700000000,
        "model": "test-model",
        "choices": [{
            "index": 0,
            "delta": {"role": "assistant", "content": "正文"},
            "finish_reason": None,
        }],
    }

    assert coalescer.push_chunk(reasoning_chunk, now_ms=0) == []
    outputs = coalescer.push_chunk(content_chunk, now_ms=5)

    assert len(outputs) == 1
    assert outputs[0]["choices"][0]["delta"] == {
        "role": "assistant",
        "reasoning_content": "思考",
    }

    pending = coalescer.flush_pending()
    assert len(pending) == 1
    assert pending[0]["choices"][0]["delta"] == {
        "role": "assistant",
        "content": "正文",
    }
