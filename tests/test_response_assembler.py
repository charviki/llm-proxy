import logging
import pytest

from proxy.backend_client import UpstreamSSEEvent
from proxy.converter import ChunkConverterMatcher
from proxy.response_assembler import (
    assemble_chat_completion_response,
    assemble_completion_response,
)


async def _iterate_events(events):
    for event in events:
        yield event


@pytest.mark.asyncio
async def test_assemble_chat_completion_response():
    matcher = ChunkConverterMatcher({"default": "reasoning_content"}, logging.getLogger("test_logger"))
    converter = matcher.get_parser("my-model")
    events = [
        UpstreamSSEEvent(event_lines=['data: {"id":"1","model":"real-model","choices":[{"delta":{"role":"assistant"},"finish_reason":null}]}']),
        UpstreamSSEEvent(event_lines=['data: {"id":"1","model":"real-model","choices":[{"delta":{"reasoning_content":"think"},"finish_reason":null}]}']),
        UpstreamSSEEvent(event_lines=['data: {"id":"1","model":"real-model","choices":[{"delta":{"content":"hello"},"finish_reason":null}]}']),
        UpstreamSSEEvent(event_lines=['data: {"id":"1","model":"real-model","choices":[{"delta":{"tool_calls":[{"index":0,"id":"call_1","type":"function","function":{"name":"read_file","arguments":"{}"}}]},"finish_reason":null}]}']),
        UpstreamSSEEvent(event_lines=['data: {"id":"1","model":"real-model","choices":[{"delta":{},"finish_reason":"tool_calls"}]}']),
        UpstreamSSEEvent(event_lines=["data: [DONE]"]),
    ]

    response_json = await assemble_chat_completion_response(
        upstream_events=_iterate_events(events),
        converter=converter,
        custom_model_id="my-model",
        source_json=None,
    )

    assert response_json["model"] == "my-model"
    assert response_json["choices"][0]["message"]["role"] == "assistant"
    assert response_json["choices"][0]["message"]["content"] == "hello"
    assert response_json["choices"][0]["message"]["reasoning_content"] == "think"
    assert response_json["choices"][0]["message"]["tool_calls"][0]["function"]["name"] == "read_file"
    assert response_json["choices"][0]["finish_reason"] == "tool_calls"


@pytest.mark.asyncio
async def test_assemble_completion_response():
    matcher = ChunkConverterMatcher({}, logging.getLogger("test_logger"))
    converter = matcher.get_parser("my-model")
    events = [
        UpstreamSSEEvent(event_lines=['data: {"id":"1","model":"real-model","choices":[{"text":"hel","finish_reason":null}]}']),
        UpstreamSSEEvent(event_lines=['data: {"id":"1","model":"real-model","choices":[{"text":"lo","finish_reason":null}]}']),
        UpstreamSSEEvent(event_lines=['data: {"id":"1","model":"real-model","choices":[{"text":"","finish_reason":"stop"}]}']),
        UpstreamSSEEvent(event_lines=["data: [DONE]"]),
    ]

    response_json = await assemble_completion_response(
        upstream_events=_iterate_events(events),
        converter=converter,
        custom_model_id="my-model",
        source_json=None,
    )

    assert response_json["model"] == "my-model"
    assert response_json["choices"][0]["text"] == "hello"
    assert response_json["choices"][0]["finish_reason"] == "stop"


@pytest.mark.asyncio
async def test_assemble_chat_completion_response_skips_tool_call_without_index():
    logger = logging.getLogger("test_logger")
    matcher = ChunkConverterMatcher({"default": "reasoning_content"}, logger)
    converter = matcher.get_parser("my-model")
    events = [
        UpstreamSSEEvent(event_lines=['data: {"id":"1","model":"real-model","choices":[{"delta":{"content":"hello"},"finish_reason":null}]}']),
        UpstreamSSEEvent(event_lines=['data: {"id":"1","model":"real-model","choices":[{"delta":{"tool_calls":[{"id":"call_1","type":"function","function":{"name":"read_file","arguments":"{}"}}]},"finish_reason":null}]}']),
        UpstreamSSEEvent(event_lines=['data: {"id":"1","model":"real-model","choices":[{"delta":{},"finish_reason":"stop"}]}']),
    ]

    response_json = await assemble_chat_completion_response(
        upstream_events=_iterate_events(events),
        converter=converter,
        custom_model_id="my-model",
        source_json=None,
        logger=logger,
    )

    assert response_json["choices"][0]["message"]["content"] == "hello"
    assert "tool_calls" not in response_json["choices"][0]["message"]
