"""代理模块 - OpenAI 协议代理核心"""
from .stream import StreamSimulator
from .sse_coalescer import SSESemanticCoalescer
from .backend_client import BackendClient
from .stream_processor import StreamEventProcessor
from .handler import ProxyHandler
from .converter import (
    BaseChunkConverter,
    ThinkTagChunkConverter,
    ReasoningChunkConverter,
    ReasoningContentChunkConverter,
    ChunkConverterMatcher,
    create_parser,
    ReasoningContent
)
from .middleware import RecordingMiddleware
from .transport import ProxyTransport, ReplayMiddleware
from .context import (
    get_replay_id,
    set_replay_id,
    clear_replay_id,
)
from .recording_interceptor import TransportRecordingMiddleware
from .recorder import RecordingContext

__all__ = [
    "StreamSimulator",
    "SSESemanticCoalescer",
    "BackendClient",
    "StreamEventProcessor",
    "ProxyHandler",
    "BaseChunkConverter",
    "ThinkTagChunkConverter",
    "ReasoningChunkConverter",
    "ReasoningContentChunkConverter",
    "ChunkConverterMatcher",
    "create_parser",
    "ReasoningContent",
    "RecordingMiddleware",
    "ProxyTransport",
    "ReplayMiddleware",
    "TransportRecordingMiddleware",
    "RecordingContext",
    "get_replay_id",
    "set_replay_id",
    "clear_replay_id",
]
