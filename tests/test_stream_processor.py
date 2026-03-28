import json
import logging

from config.models import SSECoalescingConfig
from proxy.backend_client import UpstreamSSEEvent
from proxy.converter import ChunkConverterMatcher
from proxy.sse_coalescer import SSESemanticCoalescer
from proxy.stream_processor import StreamEventProcessor


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
