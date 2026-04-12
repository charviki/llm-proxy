"""流量录制器核心逻辑"""
import json
import time
import uuid
from contextvars import ContextVar
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

# contextvars 用于在异步链路中共享状态
_recording_context: ContextVar[dict] = ContextVar('recording_context', default=None)

RECORDINGS_DIR = Path("recordings")


def get_recording_context() -> dict:
    """获取当前录制上下文"""
    return _recording_context.get() or {}


def set_recording_context(ctx: Optional[dict]) -> None:
    """设置录制上下文"""
    _recording_context.set(ctx)


def clear_recording_context() -> None:
    """清除录制上下文"""
    _recording_context.set(None)


def generate_prefix(path: str) -> tuple[str, str]:
    """生成录制文件前缀和后缀

    Returns:
        (prefix, suffix)
        例如: ("v1_chat_completions", "1742889000_abc123")
    """
    # 清理路径：移除前导斜杠，替换斜杠为下划线
    prefix = path.lstrip('/').replace('/', '_')
    timestamp = int(time.time())
    random_suffix = uuid.uuid4().hex[:6]
    return prefix, f"{timestamp}_{random_suffix}"


def ensure_recordings_dir() -> Path:
    """确保 recordings 目录存在"""
    RECORDINGS_DIR.mkdir(parents=True, exist_ok=True)
    return RECORDINGS_DIR


def mask_headers(headers: Optional[dict]) -> dict:
    """对 Header 中的敏感 Token 进行脱敏处理"""
    if not headers:
        return {}
    masked = {}
    sensitive_keys = {"authorization", "api-key", "x-api-key"}
    for k, v in headers.items():
        if k.lower() in sensitive_keys:
            if k.lower() == "authorization" and str(v).lower().startswith("bearer "):
                masked[k] = "Bearer ***"
            else:
                masked[k] = "***"
        else:
            masked[k] = v
    return masked


def write_request(prefix: str, suffix: str, request_type: str, endpoint: str, method: str,
                   url: str, headers: dict, body: dict) -> None:
    """写入请求录制文件

    文件格式: recordings/{prefix}__{suffix}__{type}.json
    例如: recordings/v1_chat_completions__1742889000_abc123__client_request.json
    """
    target_dir = ensure_recordings_dir()
    filename = f"{prefix}__{suffix}__{request_type.lower().replace(' ', '_')}.json"
    filepath = target_dir / filename
    data = {
        "version": "1.0",
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "type": request_type,
        "prefix": prefix,
        "suffix": suffix,
        "endpoint": endpoint,
        "method": method,
        "url": url,
        "headers": mask_headers(headers),
        "body": body,
        "error": None
    }
    with open(filepath, 'w', encoding='utf-8') as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def write_response(prefix: str, suffix: str, response_type: str, status_code: int,
                   timing_ms: float, body: Optional[dict] = None,
                   chunks: Optional[list[str]] = None, error: Optional[str] = None,
                   headers: Optional[dict] = None,
                   timing: Optional[dict] = None) -> None:
    """写入响应录制文件"""
    target_dir = ensure_recordings_dir()
    filename = f"{prefix}__{suffix}__{response_type.lower().replace(' ', '_')}.json"
    filepath = target_dir / filename
    data = {
        "version": "1.0",
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "type": response_type,
        "prefix": prefix,
        "suffix": suffix,
        "status_code": status_code,
        "timing_ms": timing_ms,
        "error": error
    }
    if timing is not None:
        data["timing"] = timing
    if headers is not None:
        data["headers"] = mask_headers(headers)
    if chunks is not None:
        data["chunks"] = chunks
    else:
        data["body"] = body

    with open(filepath, 'w', encoding='utf-8') as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


class RecordingContext:
    """录制上下文管理器，用于追踪单个请求的录制状态"""

    def __init__(self, prefix: str):
        self.prefix = prefix
        self.start_time: float = 0
        self.chunks: list[str] = []

    def __enter__(self):
        self.start_time = time.perf_counter()
        set_recording_context({"prefix": self.prefix})
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        clear_recording_context()
        return False

    def add_chunk(self, chunk: str) -> None:
        """添加流式响应 chunk"""
        self.chunks.append(chunk)

    def get_timing_ms(self) -> float:
        """获取耗时（毫秒）"""
        return (time.perf_counter() - self.start_time) * 1000
