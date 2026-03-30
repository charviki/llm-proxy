import json
import logging

from config.models import SSECoalescingConfig
from proxy.backend_client import UpstreamSSEEvent
from proxy.converter import ChunkConverterMatcher
from proxy.sse_coalescer import SSESemanticCoalescer
from proxy.stream_processor import PROCESSING_MARKER, StreamEventProcessor


def test_stream_processor_passes_through_non_data_event():
    converter = ChunkConverterMatcher({"default": "reasoning_content"}, logging.getLogger("test_logger")).get_parser("my-model")
    processor = StreamEventProcessor()

    outputs = processor.process_event(
        UpstreamSSEEvent(event_lines=["event: ping", "id: 1"]),
        converter,
    )

    assert outputs == [b"event: ping\nid: 1\n\n"]


def test_stream_processor_coalesces_content_chunks():
    converter = ChunkConverterMatcher({"default": "reasoning_content"}, logging.getLogger("test_logger")).get_parser("my-model")
    processor = StreamEventProcessor(
        coalescer=SSESemanticCoalescer(
            SSECoalescingConfig(enabled=True, window_ms=50, max_buffer_length=128)
        ),
        now_ms=lambda: 0,
    )

    first_outputs = processor.process_event(
        UpstreamSSEEvent(event_lines=['data: {"id":"1","model":"real-model","choices":[{"delta":{"content":"Hel"},"finish_reason":null}]}']),
        converter,
    )
    second_outputs = processor.process_event(
        UpstreamSSEEvent(event_lines=['data: {"id":"1","model":"real-model","choices":[{"delta":{"content":"lo"},"finish_reason":null}]}']),
        converter,
    )
    final_outputs = processor.process_event(
        UpstreamSSEEvent(event_lines=['data: {"id":"1","model":"real-model","choices":[{"delta":{},"finish_reason":"stop"}]}']),
        converter,
    )

    assert first_outputs == []
    assert second_outputs == []
    payload = json.loads(final_outputs[0].decode("utf-8").split("data: ", 1)[1])
    assert payload["choices"][0]["delta"]["content"] == "Hello"
    assert '"finish_reason":"stop"' in final_outputs[1].decode("utf-8")


def test_stream_processor_inserts_processing_before_first_content():
    converter = ChunkConverterMatcher({"default": "reasoning_content"}, logging.getLogger("test_logger")).get_parser("my-model")
    processor = StreamEventProcessor(
        coalescer=SSESemanticCoalescer(
            SSECoalescingConfig(enabled=True, window_ms=50, max_buffer_length=128)
        ),
        now_ms=lambda: 0,
        processing_enabled=True,
    )

    content_outputs = processor.process_event(
        UpstreamSSEEvent(event_lines=['data: {"id":"1","model":"real-model","choices":[{"delta":{"content":"正文"},"finish_reason":null}]}']),
        converter,
    )

    assert content_outputs[0] == PROCESSING_MARKER
    pending_outputs = processor.flush_pending()
    content_payload = json.loads(pending_outputs[0].decode("utf-8").split("data: ", 1)[1])
    assert content_payload["choices"][0]["delta"]["content"] == "正文"


def test_stream_processor_inserts_processing_before_first_content_after_reasoning():
    converter = ChunkConverterMatcher({"default": "reasoning_content"}, logging.getLogger("test_logger")).get_parser("my-model")
    processor = StreamEventProcessor(
        coalescer=SSESemanticCoalescer(
            SSECoalescingConfig(enabled=True, window_ms=50, max_buffer_length=128)
        ),
        now_ms=lambda: 0,
        processing_enabled=True,
    )

    reasoning_outputs = processor.process_event(
        UpstreamSSEEvent(event_lines=['data: {"id":"1","model":"real-model","choices":[{"delta":{"reasoning_content":"思考"},"finish_reason":null}]}']),
        converter,
    )
    content_outputs = processor.process_event(
        UpstreamSSEEvent(event_lines=['data: {"id":"1","model":"real-model","choices":[{"delta":{"content":"正文"},"finish_reason":null}]}']),
        converter,
    )

    assert reasoning_outputs == []
    reasoning_payload = json.loads(content_outputs[0].decode("utf-8").split("data: ", 1)[1])
    assert reasoning_payload["choices"][0]["delta"]["reasoning_content"] == "思考"
    assert content_outputs[1] == PROCESSING_MARKER

    pending_outputs = processor.flush_pending()
    content_payload = json.loads(pending_outputs[0].decode("utf-8").split("data: ", 1)[1])
    assert content_payload["choices"][0]["delta"]["content"] == "正文"


def test_stream_processor_inserts_processing_between_content_and_tool_calls_once():
    converter = ChunkConverterMatcher({"default": "reasoning_content"}, logging.getLogger("test_logger")).get_parser("my-model")
    processor = StreamEventProcessor(
        coalescer=SSESemanticCoalescer(
            SSECoalescingConfig(enabled=True, window_ms=50, max_buffer_length=128)
        ),
        now_ms=lambda: 0,
        processing_enabled=True,
    )

    processor.process_event(
        UpstreamSSEEvent(event_lines=['data: {"id":"1","model":"real-model","choices":[{"delta":{"content":"正文"},"finish_reason":null}]}']),
        converter,
    )
    tool_outputs = processor.process_event(
        UpstreamSSEEvent(event_lines=['data: {"id":"1","model":"real-model","choices":[{"delta":{"tool_calls":[{"index":0,"id":"call_1","type":"function","function":{"name":"Glob","arguments":"{\\"pattern\\":"}}]},"finish_reason":null}]}']),
        converter,
    )
    tool_outputs_next = processor.process_event(
        UpstreamSSEEvent(event_lines=['data: {"id":"1","model":"real-model","choices":[{"delta":{"tool_calls":[{"index":0,"function":{"arguments":"\\"*\\"}"}}]},"finish_reason":null}]}']),
        converter,
    )

    content_payload = json.loads(tool_outputs[0].decode("utf-8").split("data: ", 1)[1])
    assert content_payload["choices"][0]["delta"]["content"] == "正文"
    assert tool_outputs[1] == PROCESSING_MARKER
    assert PROCESSING_MARKER not in tool_outputs_next


def test_stream_processor_inserts_processing_before_done():
    converter = ChunkConverterMatcher({"default": "reasoning_content"}, logging.getLogger("test_logger")).get_parser("my-model")
    processor = StreamEventProcessor(
        coalescer=SSESemanticCoalescer(
            SSECoalescingConfig(enabled=True, window_ms=50, max_buffer_length=128)
        ),
        now_ms=lambda: 0,
        processing_enabled=True,
    )

    processor.process_event(
        UpstreamSSEEvent(event_lines=['data: {"id":"1","model":"real-model","choices":[{"delta":{"content":"正文"},"finish_reason":null}]}']),
        converter,
    )
    done_outputs = processor.process_event(
        UpstreamSSEEvent(event_lines=["data: [DONE]"]),
        converter,
    )

    content_payload = json.loads(done_outputs[0].decode("utf-8").split("data: ", 1)[1])
    assert content_payload["choices"][0]["delta"]["content"] == "正文"
    assert done_outputs[1] == PROCESSING_MARKER
    assert done_outputs[2] == b"data: [DONE]\n\n"


def test_stream_processor_inserts_processing_before_done_only_once():
    converter = ChunkConverterMatcher({"default": "reasoning_content"}, logging.getLogger("test_logger")).get_parser("my-model")
    processor = StreamEventProcessor(processing_enabled=True)

    first_done_outputs = processor.process_event(
        UpstreamSSEEvent(event_lines=["data: [DONE]"]),
        converter,
    )
    second_done_outputs = processor.process_event(
        UpstreamSSEEvent(event_lines=["data: [DONE]"]),
        converter,
    )

    assert first_done_outputs[0] == PROCESSING_MARKER
    assert first_done_outputs[1] == b"data: [DONE]\n\n"
    assert second_done_outputs == [b"data: [DONE]\n\n"]
