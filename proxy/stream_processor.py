import json
import time
from enum import Enum
from typing import Callable, Optional

from .backend_client import UpstreamSSEEvent
from .sse_coalescer import SSESemanticCoalescer


class ProcessingBoundaryPhase(str, Enum):
    CONTENT = "content"
    TOOL_CALLS = "tool_calls"
    DONE = "done"


# 内部哨兵直接复用客户端可见注释的字节值；外围识别到该值后负责决定是否延时。
PROCESSING_MARKER = b": PROCESSING\n\n"


class StreamEventProcessor:
    def __init__(
        self,
        coalescer: Optional[SSESemanticCoalescer] = None,
        now_ms: Optional[Callable[[], int]] = None,
        processing_enabled: bool = False,
    ):
        self.coalescer = coalescer
        self._now_ms = now_ms or self._default_now_ms
        self.processing_enabled = processing_enabled
        self._processing_emitted_for: set[ProcessingBoundaryPhase] = set()

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

    @staticmethod
    def _detect_processing_boundary_phase(chunk: dict) -> Optional[ProcessingBoundaryPhase]:
        choices = chunk.get("choices")
        if not choices:
            return None
        delta = choices[0].get("delta", {})
        # 这里只识别“会触发 PROCESSING 边界”的目标阶段，
        # 而不是做完整的语义分类：当前只关心首次出现的 content / tool_calls。
        if "tool_calls" in delta:
            return ProcessingBoundaryPhase.TOOL_CALLS
        if "content" in delta and delta["content"]:
            return ProcessingBoundaryPhase.CONTENT
        return None

    def _should_emit_processing_marker(self, phase: ProcessingBoundaryPhase) -> bool:
        if not self.processing_enabled:
            return False
        # phase 改成 Enum 后，可读性和静态约束更稳定；状态仍然保持轻量集合去重。
        return phase not in self._processing_emitted_for

    def process_event(self, upstream_event: UpstreamSSEEvent, converter) -> list[bytes]:
        data_content = upstream_event.data_content()
        if data_content is None or upstream_event.has_non_data_lines():
            outputs = self.flush_pending()
            outputs.append(self.encode_sse_event(upstream_event.event_lines))
            return outputs

        if upstream_event.is_done():
            outputs = self.flush_pending()
            # done 不依赖普通 chunk 检测，而是单独作为一个“收尾边界”处理。
            if self._should_emit_processing_marker(ProcessingBoundaryPhase.DONE):
                # done 前的 PROCESSING 需要排在已 flush 的真实内容之后、[DONE] 之前。
                outputs.append(PROCESSING_MARKER)
                self._processing_emitted_for.add(ProcessingBoundaryPhase.DONE)
            outputs.append(self.encode_sse_event(upstream_event.event_lines))
            return outputs

        processed = converter.parse(data_content)
        if processed is None:
            return []

        try:
            chunk = json.loads(processed)
        except json.JSONDecodeError:
            outputs = self.flush_pending()
            outputs.append(self.encode_data_sse(processed))
            return outputs

        current_phase = self._detect_processing_boundary_phase(chunk)
        outputs: list[bytes] = []
        if current_phase is not None and self._should_emit_processing_marker(current_phase):
            # 在首次出现 content/tool_calls 前，先把前序真实 chunk 吐给客户端，再交给外围层插入 PROCESSING。
            outputs.extend(self.flush_pending())
            outputs.append(PROCESSING_MARKER)
            self._processing_emitted_for.add(current_phase)

        if self.coalescer is None or not self.coalescer.enabled:
            outputs.append(self.encode_data_sse(processed))
        else:
            outputs.extend(
                self.encode_json_sse_chunk(flushed_chunk)
                for flushed_chunk in self.coalescer.push_chunk(chunk, now_ms=self._now_ms())
            )

        return outputs
