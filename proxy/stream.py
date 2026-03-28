"""流式响应模拟器 - 将非流式响应转换为流式响应"""
import json
import time
import logging
from typing import AsyncGenerator

from config.models import SSECoalescingConfig

from .sse_coalescer import SSESemanticCoalescer


# tool_calls 参数分块大小（与 OpenAI 流式规范对齐）
_TOOL_ARGS_CHUNK_SIZE = 64
# 文本内容分块大小
_TEXT_CHUNK_SIZE = 16


class StreamSimulator:
    """流式响应模拟器"""

    @staticmethod
    def _build_chunk_payload(response_id: str, model_id: str, created: int, delta: dict, finish_reason=None) -> dict:
        return {
            "id": response_id,
            "object": "chat.completion.chunk",
            "created": created,
            "model": model_id,
            "choices": [{
                "index": 0,
                "delta": delta,
                "finish_reason": finish_reason
            }]
        }

    @staticmethod
    def _encode_chunk(chunk_data: dict) -> bytes:
        return f"data: {json.dumps(chunk_data, ensure_ascii=False, separators=(',', ':'))}\n\n".encode('utf-8')

    @staticmethod
    def _build_chunk(response_id: str, model_id: str, created: int, delta: dict, finish_reason=None) -> bytes:
        """构建标准 OpenAI chat.completion.chunk SSE 事件"""
        return StreamSimulator._encode_chunk(
            StreamSimulator._build_chunk_payload(response_id, model_id, created, delta, finish_reason)
        )

    @staticmethod
    def _iter_simulated_fragments(text: str):
        if text == "":
            yield ""
            return
        yield from text

    @staticmethod
    def _iter_text_segments(text: str, fine_grained: bool):
        if not text:
            return
        if fine_grained:
            if "<think>" in text or "</think>" in text:
                yield text
                return
            cursor = 0
            while cursor < len(text):
                next_open = text.find("<think>", cursor)
                next_close = text.find("</think>", cursor)
                tag_positions = [pos for pos in (next_open, next_close) if pos != -1]
                if not tag_positions:
                    yield from StreamSimulator._iter_simulated_fragments(text[cursor:])
                    return
                next_tag_pos = min(tag_positions)
                if next_tag_pos > cursor:
                    yield from StreamSimulator._iter_simulated_fragments(text[cursor:next_tag_pos])
                if next_tag_pos == next_open:
                    yield "<think>"
                    cursor = next_tag_pos + len("<think>")
                else:
                    yield "</think>"
                    cursor = next_tag_pos + len("</think>")
            return
        for i in range(0, len(text), _TEXT_CHUNK_SIZE):
            yield text[i:i + _TEXT_CHUNK_SIZE]

    @staticmethod
    def _iter_tool_argument_segments(arguments: str, fine_grained: bool):
        if fine_grained:
            for fragment_index, fragment in enumerate(StreamSimulator._iter_simulated_fragments(arguments)):
                yield fragment_index, fragment
            return
        first_args_chunk = arguments[:_TOOL_ARGS_CHUNK_SIZE]
        yield 0, first_args_chunk
        remaining_args = arguments[_TOOL_ARGS_CHUNK_SIZE:]
        fragment_index = 1
        for i in range(0, len(remaining_args), _TOOL_ARGS_CHUNK_SIZE):
            yield fragment_index, remaining_args[i:i + _TOOL_ARGS_CHUNK_SIZE]
            fragment_index += 1

    @staticmethod
    def iter_chat_completion_chunk_payloads(
        response_json: dict,
        model_id: str,
        fine_grained: bool = False,
    ):
        response_id = response_json.get("id", f"chatcmpl-sim-{int(time.time())}")
        created = response_json.get("created", int(time.time()))

        message = response_json["choices"][0]["message"]
        content = message.get("content") or ""
        reasoning = message.get("reasoning") or ""
        reasoning_content = message.get("reasoning_content") or ""
        tool_calls = message.get("tool_calls")

        yield StreamSimulator._build_chunk_payload(response_id, model_id, created, {"role": "assistant"})

        if reasoning:
            for chunk in StreamSimulator._iter_text_segments(reasoning, fine_grained):
                yield StreamSimulator._build_chunk_payload(response_id, model_id, created, {"reasoning": chunk})

        if reasoning_content:
            for chunk in StreamSimulator._iter_text_segments(reasoning_content, fine_grained):
                yield StreamSimulator._build_chunk_payload(response_id, model_id, created, {"reasoning_content": chunk})

        if content:
            for chunk in StreamSimulator._iter_text_segments(content, fine_grained):
                yield StreamSimulator._build_chunk_payload(response_id, model_id, created, {"content": chunk})

        if tool_calls:
            for idx, tool_call in enumerate(tool_calls):
                func = tool_call.get("function", {})
                func_name = func.get("name", "")
                func_args = func.get("arguments", "")

                for fragment_index, fragment in StreamSimulator._iter_tool_argument_segments(func_args, fine_grained):
                    tool_delta = {
                        "tool_calls": [{
                            "index": idx,
                            "function": {
                                "arguments": fragment
                            }
                        }]
                    }
                    if fragment_index == 0:
                        tool_delta["tool_calls"][0]["id"] = tool_call.get("id", "")
                        tool_delta["tool_calls"][0]["type"] = tool_call.get("type", "function")
                        tool_delta["tool_calls"][0]["function"]["name"] = func_name
                    yield StreamSimulator._build_chunk_payload(response_id, model_id, created, tool_delta)

        finish_reason = "tool_calls" if tool_calls else "stop"
        yield StreamSimulator._build_chunk_payload(response_id, model_id, created, {}, finish_reason=finish_reason)

    @staticmethod
    async def simulate_chat_completion(
        response_json: dict,
        model_id: str,
        logger: logging.Logger,
        sse_coalescing_config: SSECoalescingConfig | None = None,
    ) -> AsyncGenerator[bytes, None]:
        """模拟 chat completions 流式响应"""
        try:
            encode = StreamSimulator._encode_chunk
            coalescing_config = sse_coalescing_config or SSECoalescingConfig()

            if coalescing_config.enabled:
                coalescer = SSESemanticCoalescer(coalescing_config)
                for payload in StreamSimulator.iter_chat_completion_chunk_payloads(
                    response_json,
                    model_id,
                    fine_grained=True,
                ):
                    delta = payload["choices"][0]["delta"]
                    if not delta or "reasoning" in delta or "reasoning_content" in delta or "role" in delta:
                        for chunk in coalescer.flush_pending():
                            yield encode(chunk)
                        yield encode(payload)
                        continue
                    for chunk in coalescer.push_chunk(payload, now_ms=0):
                        yield encode(chunk)

                for chunk in coalescer.flush_pending():
                    yield encode(chunk)

                yield b'data: [DONE]\n\n'
                return

            for payload in StreamSimulator.iter_chat_completion_chunk_payloads(
                response_json,
                model_id,
                fine_grained=False,
            ):
                yield encode(payload)

            yield b'data: [DONE]\n\n'

        except Exception as e:
            logger.exception(f"模拟流式响应失败: {e}")
            error_data = json.dumps({
                "error": {
                    "message": f"Proxy error: failed to simulate stream: {str(e)}",
                    "type": "server_error",
                    "code": "proxy_stream_simulation_error"
                }
            }, ensure_ascii=False)
            yield f'data: {error_data}\n\n'.encode('utf-8')

    @staticmethod
    async def simulate_completions(
        response_json: dict,
        model_id: str,
        logger: logging.Logger
    ) -> AsyncGenerator[bytes, None]:
        """模拟 completions 流式响应"""
        try:
            response_id = response_json.get("id", f"cmpl-sim-{int(time.time())}")
            created = response_json.get("created", int(time.time()))

            content = response_json.get("choices", [{}])[0].get("text", "")

            def build_completion_chunk(text: str, finish_reason=None) -> bytes:
                chunk_data = {
                    "id": response_id,
                    "object": "text_completion",
                    "created": created,
                    "model": model_id,
                    "choices": [{
                        "index": 0,
                        "text": text,
                        "finish_reason": finish_reason
                    }]
                }
                return f"data: {json.dumps(chunk_data, ensure_ascii=False, separators=(',', ':'))}\n\n".encode('utf-8')

            # initial chunk
            yield build_completion_chunk("")

            # content chunks
            for i in range(0, len(content), _TEXT_CHUNK_SIZE):
                chunk = content[i:i + _TEXT_CHUNK_SIZE]
                yield build_completion_chunk(chunk)

            # finish chunk
            yield build_completion_chunk("", finish_reason="stop")

            yield b'data: [DONE]\n\n'

        except Exception as e:
            logger.exception(f"模拟 completions 流式响应失败: {e}")
            error_data = json.dumps({
                "error": {
                    "message": f"Proxy error: failed to simulate stream: {str(e)}",
                    "type": "server_error",
                    "code": "proxy_stream_simulation_error"
                }
            }, ensure_ascii=False)
            yield f'data: {error_data}\n\n'.encode('utf-8')
