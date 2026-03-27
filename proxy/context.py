"""请求上下文管理 - 用于在请求生命周期内传递 replay_id 和录制上下文"""
from contextvars import ContextVar
from typing import Optional

_replay_id: ContextVar[str | None] = ContextVar("replay_id", default=None)
_recording_context: ContextVar[Optional[dict]] = ContextVar("recording_context", default=None)


def get_replay_id() -> str | None:
    """获取当前请求的 replay_id"""
    return _replay_id.get()


def set_replay_id(replay_id: str):
    """设置当前请求的 replay_id"""
    _replay_id.set(replay_id)


def clear_replay_id():
    """清除 replay_id"""
    _replay_id.set(None)


def get_recording_ctx() -> dict:
    """获取当前录制上下文"""
    return _recording_context.get() or {}


def set_recording_context(ctx: Optional[dict]):
    """设置录制上下文"""
    _recording_context.set(ctx)


def clear_recording_ctx():
    """清除录制上下文"""
    _recording_context.set(None)
