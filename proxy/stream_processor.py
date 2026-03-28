import json
import time
from typing import Callable, Optional

from .backend_client import UpstreamSSEEvent
from .sse_coalescer import SSESemanticCoalescer


class StreamEventProcessor:
    def __init__(
        self,
        coalescer: Optional[SSESemanticCoalescer] = None,
        now_ms: Optional[Callable[[], int]] = None,
    ):
        self.coalescer = coalescer
        self._now_ms = now_ms or self._default_now_ms

    @staticmethod
    def _default_now_ms() -> int:
        return time.monotonic_ns() // 1_000_000

    @staticmethod
    def encode_sse_event(event_lines: list[str]) -> bytes:
        return ("\n".join(event_lines) + "\n\n").encode("utf-8")

    @staticmethod
    def encode_data_sse(data_content: str) -> bytes:
        return "".join(f"data: {line}\n" for line in data_content.splitlines() or [""]).encode("utf-8") + b"\n"

    @classmethod
    def encode_json_sse_chunk(cls, chunk: dict) -> bytes:
        return cls.encode_data_sse(
            json.dumps(chunk, ensure_ascii=False, separators=(",", ":"))
        )

    def flush_pending(self) -> list[bytes]:
        if self.coalescer is None:
            return []
        return [
            self.encode_json_sse_chunk(chunk)
            for chunk in self.coalescer.flush_pending()
        ]

    def process_event(self, upstream_event: UpstreamSSEEvent, converter) -> list[bytes]:
        data_content = upstream_event.data_content()
        if data_content is None or upstream_event.has_non_data_lines():
            outputs = self.flush_pending()
            outputs.append(self.encode_sse_event(upstream_event.event_lines))
            return outputs

        if upstream_event.is_done():
            outputs = self.flush_pending()
            outputs.append(self.encode_sse_event(upstream_event.event_lines))
            return outputs

        processed = converter.parse(data_content)
        if processed is None:
            return []

        if self.coalescer is None or not self.coalescer.enabled:
            return [self.encode_data_sse(processed)]

        try:
            chunk = json.loads(processed)
        except json.JSONDecodeError:
            outputs = self.flush_pending()
            outputs.append(self.encode_data_sse(processed))
            return outputs

        return [
            self.encode_json_sse_chunk(flushed_chunk)
            for flushed_chunk in self.coalescer.push_chunk(chunk, now_ms=self._now_ms())
        ]
