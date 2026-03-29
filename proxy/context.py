"""请求上下文管理 - 用于在请求生命周期内传递 replay_id 和录制上下文"""
from contextvars import ContextVar

_replay_id: ContextVar[str | None] = ContextVar("replay_id", default=None)


def get_replay_id() -> str | None:
    """获取当前请求的 replay_id"""
    return _replay_id.get()


def set_replay_id(replay_id: str):
    """设置当前请求的 replay_id"""
    _replay_id.set(replay_id)


def clear_replay_id():
    """清除 replay_id"""
    _replay_id.set(None)
