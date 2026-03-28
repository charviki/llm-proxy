from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from config.models import SSECoalescingConfig


@dataclass
class _ChunkEnvelope:
    top_level: dict
    choice: dict


@dataclass
class _TextBuffer:
    envelope: _ChunkEnvelope
    field_name: str
    text: str
    # 例如 role 这类稳定的标量字段需要跟随合包结果一起发给客户端。
    extra_delta: dict
    started_at_ms: int


@dataclass
class _ToolCallBuffer:
    envelope: _ChunkEnvelope
    tool_index: int
    arguments: str
    # tool_calls 首段之外也可能携带 role 等上下文，flush 时需要保留。
    extra_delta: dict
    started_at_ms: int
    tool_call_id: str
    tool_call_type: str
    function_name: str


class SSESemanticCoalescer:
    def __init__(self, config: SSECoalescingConfig):
        self.enabled = config.enabled
        self.window_ms = config.window_ms
        self.max_buffer_length = config.max_buffer_length
        self._buffer: Optional[_TextBuffer | _ToolCallBuffer] = None
        self._emitted_tool_headers: set[int] = set()

    @property
    def has_pending(self) -> bool:
        return self._buffer is not None

    def next_flush_deadline_ms(self) -> Optional[int]:
        if self._buffer is None:
            return None
        return self._buffer.started_at_ms + self.window_ms

    def flush_expired(self, now_ms: int) -> list[dict]:
        if not self.enabled or self._buffer is None:
            return []
        # 只在窗口超时后主动 flush；正常情况下更倾向于等语义边界或长度阈值触发。
        if now_ms < self._buffer.started_at_ms + self.window_ms:
            return []
        return self.flush_pending()

    def flush_pending(self) -> list[dict]:
        if self._buffer is None:
            return []

        buffer = self._buffer
        self._buffer = None

        if isinstance(buffer, _TextBuffer):
            delta = dict(buffer.extra_delta)
            delta[buffer.field_name] = buffer.text
            return [self._build_chunk(buffer.envelope, delta)]

        tool_delta = {
            "tool_calls": [{
                "index": buffer.tool_index,
                "function": {
                    "arguments": buffer.arguments
                }
            }]
        }

        if buffer.tool_index not in self._emitted_tool_headers:
            tool_delta["tool_calls"][0]["id"] = buffer.tool_call_id
            tool_delta["tool_calls"][0]["type"] = buffer.tool_call_type
            tool_delta["tool_calls"][0]["function"]["name"] = buffer.function_name
            self._emitted_tool_headers.add(buffer.tool_index)

        # 例如 role 这类字段不能在 tool_calls 合包后丢失，否则客户端看到的 delta 结构会回退。
        tool_delta.update(buffer.extra_delta)
        return [self._build_chunk(buffer.envelope, tool_delta)]

    def push_chunk(self, chunk: dict, now_ms: int) -> list[dict]:
        if not self.enabled:
            return [chunk]

        outputs = self.flush_expired(now_ms)

        if not self._is_chat_chunk(chunk):
            outputs.extend(self.flush_pending())
            outputs.append(chunk)
            return outputs

        choices = chunk["choices"]
        if len(choices) != 1:
            outputs.extend(self.flush_pending())
            outputs.append(chunk)
            return outputs

        choice = choices[0]
        if choice.get("finish_reason") is not None:
            outputs.extend(self.flush_pending())
            outputs.append(chunk)
            return outputs

        delta = choice.get("delta")
        if not isinstance(delta, dict) or not delta:
            outputs.extend(self.flush_pending())
            outputs.append(chunk)
            return outputs

        # 文本优先于 tool_calls 识别，因为 reasoning/content 更容易连续出现，且需要共享同一套窗口策略。
        text_state = self._extract_text_delta(delta)
        if text_state is not None:
            outputs.extend(self._push_text(chunk, text_state, now_ms))
            return outputs

        tool_state = self._extract_tool_call(delta)
        if tool_state is not None:
            outputs.extend(self._push_tool_call(chunk, tool_state, now_ms))
            return outputs

        outputs.extend(self.flush_pending())
        outputs.append(chunk)
        return outputs

    def _push_text(self, chunk: dict, text_state: tuple[str, str, dict], now_ms: int) -> list[dict]:
        field_name, text, extra_delta = text_state
        if isinstance(self._buffer, _ToolCallBuffer):
            flushed = self.flush_pending()
        else:
            flushed = []

        if self._buffer is None:
            self._buffer = _TextBuffer(
                envelope=self._capture_envelope(chunk),
                field_name=field_name,
                text=text,
                extra_delta=dict(extra_delta),
                started_at_ms=now_ms,
            )
        else:
            merged_extra_delta = self._merge_extra_delta(self._buffer.extra_delta, extra_delta)
            # 文本字段类型变化（reasoning_content -> content）或上下文字段不兼容时，必须先 flush 再开启新 buffer。
            if self._buffer.field_name != field_name or merged_extra_delta is None:
                flushed.extend(self.flush_pending())
                self._buffer = _TextBuffer(
                    envelope=self._capture_envelope(chunk),
                    field_name=field_name,
                    text=text,
                    extra_delta=dict(extra_delta),
                    started_at_ms=now_ms,
                )
            else:
                self._buffer.extra_delta = merged_extra_delta
                self._buffer.text += text

        if len(self._buffer.text) >= self.max_buffer_length:
            flushed.extend(self.flush_pending())

        return flushed

    def _push_tool_call(self, chunk: dict, tool_state: tuple[int, str, str, str, str, dict], now_ms: int) -> list[dict]:
        tool_index, tool_call_id, tool_call_type, function_name, arguments, extra_delta = tool_state

        if isinstance(self._buffer, _TextBuffer):
            flushed = self.flush_pending()
        elif isinstance(self._buffer, _ToolCallBuffer) and self._buffer.tool_index != tool_index:
            flushed = self.flush_pending()
        else:
            flushed = []

        if self._buffer is None:
            self._buffer = _ToolCallBuffer(
                envelope=self._capture_envelope(chunk),
                tool_index=tool_index,
                arguments=arguments,
                extra_delta=dict(extra_delta),
                started_at_ms=now_ms,
                tool_call_id=tool_call_id,
                tool_call_type=tool_call_type,
                function_name=function_name,
            )
        else:
            merged_extra_delta = self._merge_extra_delta(self._buffer.extra_delta, extra_delta)
            if merged_extra_delta is None:
                # 同一 tool index 但上下文字段发生冲突时，宁可切成两个语义块，也不要错误合并。
                flushed.extend(self.flush_pending())
                self._buffer = _ToolCallBuffer(
                    envelope=self._capture_envelope(chunk),
                    tool_index=tool_index,
                    arguments=arguments,
                    extra_delta=dict(extra_delta),
                    started_at_ms=now_ms,
                    tool_call_id=tool_call_id,
                    tool_call_type=tool_call_type,
                    function_name=function_name,
                )
                if len(self._buffer.arguments) >= self.max_buffer_length:
                    flushed.extend(self.flush_pending())
                return flushed
            self._buffer.extra_delta = merged_extra_delta
            self._buffer.arguments += arguments
            if tool_call_id:
                self._buffer.tool_call_id = tool_call_id
            if tool_call_type:
                self._buffer.tool_call_type = tool_call_type
            if function_name:
                self._buffer.function_name = function_name

        if len(self._buffer.arguments) >= self.max_buffer_length:
            flushed.extend(self.flush_pending())

        return flushed

    @staticmethod
    def _is_chat_chunk(chunk: dict) -> bool:
        return isinstance(chunk, dict) and isinstance(chunk.get("choices"), list)

    @staticmethod
    def _extract_text_delta(delta: dict) -> Optional[tuple[str, str, dict]]:
        text_field_names = [field_name for field_name in ("content", "reasoning_content") if field_name in delta]
        if len(text_field_names) != 1:
            return None
        field_name = text_field_names[0]
        text = delta.get(field_name)
        if not isinstance(text, str):
            return None
        extra_delta = {}
        for key, value in delta.items():
            if key == field_name:
                continue
            # 这里仅接受可稳定复用的标量字段；复杂结构交给上层直接透传，避免错误语义合并。
            if isinstance(value, (dict, list)):
                return None
            extra_delta[key] = value
        return field_name, text, extra_delta

    @staticmethod
    def _extract_tool_call(delta: dict) -> Optional[tuple[int, str, str, str, str, dict]]:
        tool_calls = delta.get("tool_calls")
        if tool_calls is None:
            return None

        if not isinstance(tool_calls, list) or len(tool_calls) != 1:
            return None

        tool_call = tool_calls[0]
        if not isinstance(tool_call, dict):
            return None

        tool_index = tool_call.get("index")
        if not isinstance(tool_index, int):
            return None

        function = tool_call.get("function")
        if not isinstance(function, dict):
            return None

        arguments = function.get("arguments")
        if not isinstance(arguments, str):
            return None

        tool_call_id = tool_call.get("id") or ""
        tool_call_type = tool_call.get("type") or "function"
        function_name = function.get("name") or ""
        extra_delta = {}
        for key, value in delta.items():
            if key == "tool_calls":
                continue
            # 与文本合包保持一致，只保留可安全跨 chunk 复用的标量上下文。
            if isinstance(value, (dict, list)):
                return None
            extra_delta[key] = value
        return tool_index, tool_call_id, tool_call_type, function_name, arguments, extra_delta

    @staticmethod
    def _capture_envelope(chunk: dict) -> _ChunkEnvelope:
        choice = chunk["choices"][0]
        return _ChunkEnvelope(
            top_level={key: value for key, value in chunk.items() if key != "choices"},
            choice={key: value for key, value in choice.items() if key not in {"delta", "finish_reason"}},
        )

    @staticmethod
    def _build_chunk(envelope: _ChunkEnvelope, delta: dict) -> dict:
        chunk = dict(envelope.top_level)
        choice = dict(envelope.choice)
        choice["delta"] = delta
        choice["finish_reason"] = None
        chunk["choices"] = [choice]
        return chunk

    @staticmethod
    def _merge_extra_delta(existing: dict, incoming: dict) -> Optional[dict]:
        merged = dict(existing)
        for key, value in incoming.items():
            # 同一语义段里如果附加字段值不一致，说明语义边界已经变化，不能继续合包。
            if key in merged and merged[key] != value:
                return None
            merged[key] = value
        return merged
