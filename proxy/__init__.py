"""代理模块 - OpenAI 协议代理核心"""
from .stream import StreamSimulator
from .handler import ProxyHandler
from .converter import (
    BaseChunkConverter,
    ThinkTagChunkConverter,
    GeminiChunkConverter,
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
from .interceptors import Interceptor
from .recording_interceptor import TransportRecordingMiddleware
from .recorder import RecordingContext

__all__ = [
    "StreamSimulator",
    "ProxyHandler",
    "BaseChunkConverter",
    "ThinkTagChunkConverter",
    "GeminiChunkConverter",
    "ReasoningContentChunkConverter",
    "ChunkConverterMatcher",
    "create_parser",
    "ReasoningContent",
    "RecordingMiddleware",
    "ProxyTransport",
    "ReplayMiddleware",
    "Interceptor",
    "TransportRecordingMiddleware",
    "RecordingContext",
    "get_replay_id",
    "set_replay_id",
    "clear_replay_id",
]
