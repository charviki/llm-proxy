"""流式响应模拟器 - 将非流式响应转换为流式响应"""
import json
import time
import logging
from typing import AsyncGenerator


# tool_calls 参数分块大小（与 OpenAI 流式规范对齐）
_TOOL_ARGS_CHUNK_SIZE = 64
# 文本内容分块大小
_TEXT_CHUNK_SIZE = 16


class StreamSimulator:
    """流式响应模拟器"""

    @staticmethod
    def _build_chunk(response_id: str, model_id: str, created: int, delta: dict, finish_reason=None) -> bytes:
        """构建标准 OpenAI chat.completion.chunk SSE 事件"""
        chunk_data = {
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
        return f"data: {json.dumps(chunk_data, ensure_ascii=False, separators=(',', ':'))}\n\n".encode('utf-8')

    @staticmethod
    async def simulate_chat_completion(
        response_json: dict,
        model_id: str,
        logger: logging.Logger
    ) -> AsyncGenerator[bytes, None]:
        """模拟 chat completions 流式响应"""
        try:
            # 复用原始响应的元数据
            response_id = response_json.get("id", f"chatcmpl-sim-{int(time.time())}")
            created = response_json.get("created", int(time.time()))

            message = response_json["choices"][0]["message"]
            content = message.get("content") or ""
            reasoning_content = message.get("reasoning_content") or ""
            tool_calls = message.get("tool_calls")

            build = StreamSimulator._build_chunk

            # 1. role chunk
            yield build(response_id, model_id, created, {"role": "assistant"})

            # 2. reasoning_content chunks
            if reasoning_content:
                for i in range(0, len(reasoning_content), _TEXT_CHUNK_SIZE):
                    chunk = reasoning_content[i:i + _TEXT_CHUNK_SIZE]
                    yield build(response_id, model_id, created, {"reasoning_content": chunk})

            # 3. content chunks
            if content:
                for i in range(0, len(content), _TEXT_CHUNK_SIZE):
                    chunk = content[i:i + _TEXT_CHUNK_SIZE]
                    yield build(response_id, model_id, created, {"content": chunk})

            # 4. tool_calls chunks（按 OpenAI 流式 tool_calls 规范）
            if tool_calls:
                for idx, tool_call in enumerate(tool_calls):
                    func = tool_call.get("function", {})
                    func_name = func.get("name", "")
                    func_args = func.get("arguments", "")

                    # 首块：发送 index、id、type、function.name 和 arguments 的第一段
                    first_args_chunk = func_args[:_TOOL_ARGS_CHUNK_SIZE]
                    yield build(response_id, model_id, created, {
                        "tool_calls": [{
                            "index": idx,
                            "id": tool_call.get("id", ""),
                            "type": "function",
                            "function": {
                                "name": func_name,
                                "arguments": first_args_chunk
                            }
                        }]
                    })

                    # 续块：分段发送剩余 arguments
                    remaining_args = func_args[_TOOL_ARGS_CHUNK_SIZE:]
                    for i in range(0, len(remaining_args), _TOOL_ARGS_CHUNK_SIZE):
                        args_chunk = remaining_args[i:i + _TOOL_ARGS_CHUNK_SIZE]
                        yield build(response_id, model_id, created, {
                            "tool_calls": [{
                                "index": idx,
                                "function": {
                                    "arguments": args_chunk
                                }
                            }]
                        })

            # 5. finish chunk
            finish_reason = "tool_calls" if tool_calls else "stop"
            yield build(response_id, model_id, created, {}, finish_reason=finish_reason)

            # 6. [DONE]
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
