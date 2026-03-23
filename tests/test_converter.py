import pytest
import json
from proxy.converter import (
    ReasoningContent,
    BaseChunkConverter,
    ThinkTagChunkConverter,
    GeminiChunkConverter,
    ReasoningContentChunkConverter,
    ChunkConverterMatcher,
    create_parser,
    ThinkState
)

def test_think_tag_converter():
    # 测试有前缀正文的情况（目前约定这种情况下就不提取后续标签了）
    converter_prefix = ThinkTagChunkConverter("test-model")
    res = converter_prefix.process_chunk({"content": "Hello "})
    assert res.content == "Hello "
    assert res.reasoning == ""
    assert converter_prefix.think_state == ThinkState.FINISHED

    # 新起一个请求测试正常的 Think tag 流
    converter = ThinkTagChunkConverter("test-model")

    # Think tag start (必须在第一个有效 content chunk 里)
    res = converter.process_chunk({"content": "<think>This is "})
    assert res.content == ""
    assert res.reasoning == "This is "

    # Inside think
    res = converter.process_chunk({"content": "thinking"})
    assert res.content == ""
    assert res.reasoning == "thinking"

    # Think tag end
    res = converter.process_chunk({"content": "</think>World"})
    assert res.content == "World"
    assert res.reasoning == ""
    assert converter.think_state == ThinkState.FINISHED

    # Test single chunk with full think tags
    conv2 = ThinkTagChunkConverter("test-model")
    res2 = conv2.process_chunk({"content": "A<think>B</think>C"})
    assert res2.content == "AC"
    assert res2.reasoning == "B"
    assert conv2.think_state == ThinkState.FINISHED

def test_gemini_reasoning_converter():
    converter = GeminiChunkConverter("test-model")
    delta = {"reasoning": "thought", "reasoning_details": {}, "content": "text"}

    res = converter.process_chunk(delta)
    assert res.reasoning == "thought"
    assert res.content == "text"
    # Ensure it's popped
    assert "reasoning" not in delta

def test_reasoning_content_converter():
    converter = ReasoningContentChunkConverter("test-model")
    delta = {"reasoning_content": "some thinking", "content": "actual content"}

    res = converter.process_chunk(delta)
    assert res.reasoning == "some thinking"
    assert res.content == "actual content"
    assert "reasoning_content" not in delta

def test_create_parser():
    assert isinstance(create_parser("think_tag", "m"), ThinkTagChunkConverter)
    assert isinstance(create_parser("reasoning", "m"), GeminiChunkConverter)
    assert isinstance(create_parser("reasoning_content", "m"), ReasoningContentChunkConverter)
    assert isinstance(create_parser("unknown_type", "m"), ReasoningContentChunkConverter)

def test_chunk_converter_matcher():
    config = {
        "claude": "think_tag",
        "gemini": "reasoning",
        "deepseek": "reasoning_content",
        "default": "reasoning_content"
    }
    matcher = ChunkConverterMatcher(config)

    # Match keywords
    assert isinstance(matcher.get_parser("anthropic/claude-3"), ThinkTagChunkConverter)
    assert isinstance(matcher.get_parser("google/gemini-1.5-pro"), GeminiChunkConverter)
    assert isinstance(matcher.get_parser("deepseek-reasoner"), ReasoningContentChunkConverter)

    # Unmatched should return BaseChunkConverter (standard model)
    fallback = matcher.get_parser("openai/gpt-4o")
    assert fallback.__class__.__name__ == "BaseChunkConverter"


# ===== 新增：tool_calls 相关测试 =====

def test_base_converter_parse_replaces_model_id():
    """BaseChunkConverter.parse() 应替换 model ID"""
    converter = BaseChunkConverter("custom/my-model")
    data_str = json.dumps({
        "id": "chatcmpl-123",
        "model": "backend-model",
        "choices": [{"delta": {"content": "hello"}, "finish_reason": None}]
    })
    result = converter.parse(data_str)
    parsed = json.loads(result)
    assert parsed["model"] == "custom/my-model"
    assert parsed["choices"][0]["delta"]["content"] == "hello"

def test_base_converter_parse_preserves_tool_calls():
    """BaseChunkConverter.parse() 应透传 tool_calls delta"""
    converter = BaseChunkConverter("custom/my-model")
    data_str = json.dumps({
        "id": "chatcmpl-123",
        "model": "backend-model",
        "choices": [{
            "delta": {
                "tool_calls": [{
                    "index": 0,
                    "id": "call_abc",
                    "type": "function",
                    "function": {"name": "read_file", "arguments": '{"path": "/tmp/test"}'}
                }]
            },
            "finish_reason": None
        }]
    })
    result = converter.parse(data_str)
    parsed = json.loads(result)
    assert parsed["model"] == "custom/my-model"
    assert parsed["choices"][0]["delta"]["tool_calls"][0]["function"]["name"] == "read_file"

def test_reasoning_converter_preserves_tool_calls_delta():
    """AbstractReasoningConverter.parse() 不应丢弃包含 tool_calls 的 delta"""
    converter = GeminiChunkConverter("custom/gemini-model")
    # 模拟只有 tool_calls 的 delta（无 content、无 reasoning）
    data_str = json.dumps({
        "id": "chatcmpl-123",
        "model": "gemini-2.0",
        "choices": [{
            "delta": {
                "tool_calls": [{
                    "index": 0,
                    "id": "call_xyz",
                    "type": "function",
                    "function": {"name": "execute_command", "arguments": ""}
                }]
            },
            "finish_reason": None
        }]
    })
    result = converter.parse(data_str)
    assert result is not None, "tool_calls delta should NOT be discarded"
    parsed = json.loads(result)
    assert parsed["model"] == "custom/gemini-model"
    assert parsed["choices"][0]["delta"]["tool_calls"][0]["function"]["name"] == "execute_command"

def test_reasoning_converter_preserves_tool_calls_with_reasoning():
    """当 delta 同时包含 reasoning 和 tool_calls 时，清理 reasoning 后应保留 tool_calls"""
    converter = GeminiChunkConverter("custom/model")
    data_str = json.dumps({
        "id": "chatcmpl-456",
        "model": "backend-model",
        "choices": [{
            "delta": {
                "reasoning": "let me think...",
                "reasoning_details": {"some": "data"},
                "tool_calls": [{
                    "index": 0,
                    "function": {"arguments": '{"key": "value"}'}
                }]
            },
            "finish_reason": None
        }]
    })
    result = converter.parse(data_str)
    assert result is not None
    parsed = json.loads(result)
    delta = parsed["choices"][0]["delta"]
    # reasoning 私有字段应被清理
    assert "reasoning" not in delta
    assert "reasoning_details" not in delta
    # tool_calls 和 reasoning_content 应保留
    assert "tool_calls" in delta
    assert delta["tool_calls"][0]["function"]["arguments"] == '{"key": "value"}'

def test_reasoning_converter_model_id_remapping():
    """流式响应中 model ID 应被替换为客户端请求的自定义模型名"""
    converter = ReasoningContentChunkConverter("my-custom/deepseek-r1")
    data_str = json.dumps({
        "id": "chatcmpl-789",
        "model": "deepseek-r1-internal",
        "choices": [{
            "delta": {"reasoning_content": "thinking...", "content": ""},
            "finish_reason": None
        }]
    })
    result = converter.parse(data_str)
    parsed = json.loads(result)
    assert parsed["model"] == "my-custom/deepseek-r1"

def test_reasoning_converter_finished_state_model_remapping():
    """思考结束后的极速透传路径也应做 model ID 回映射"""
    converter = ThinkTagChunkConverter("my-custom/claude")
    # 先让 converter 进入 FINISHED 状态
    converter.think_state = ThinkState.FINISHED

    data_str = json.dumps({
        "id": "chatcmpl-999",
        "model": "claude-3-opus",
        "choices": [{
            "delta": {"content": "hello world"},
            "finish_reason": None
        }]
    })
    result = converter.parse(data_str)
    parsed = json.loads(result)
    assert parsed["model"] == "my-custom/claude"
