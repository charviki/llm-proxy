import pytest
from proxy.converter import (
    ReasoningContent,
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
