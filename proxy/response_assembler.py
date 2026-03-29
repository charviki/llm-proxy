"""统一响应聚合器 - 把内部事件流重新组装为非流式 JSON。"""
import copy
import json
import logging
from typing import Optional

from .backend_client import UpstreamSSEEvent, UpstreamStreamItem


def parse_processed_chunk(
    upstream_event: UpstreamSSEEvent,
    converter,
    logger: Optional[logging.Logger] = None,
) -> Optional[dict]:
    """对单个 SSE 事件执行 converter 处理并解析成 JSON chunk。"""
    data_content = upstream_event.data_content()
    if data_content is None or upstream_event.is_done():
        if logger is not None and upstream_event.event_lines and not upstream_event.is_done():
            logger.debug(f"跳过无法聚合的 SSE 事件: {upstream_event.event_lines}")
        return None
    processed = converter.parse(data_content)
    if processed is None:
        return None
    try:
        return json.loads(processed)
    except json.JSONDecodeError:
        if logger is not None:
            logger.warning(f"聚合阶段解析 chunk JSON 失败: {processed[:200]}")
        return None


class ChatCompletionResponseAssembler:
    """聚合 chat.completions 事件流。"""

    def __init__(self, custom_model_id: str, source_json: Optional[dict], logger: Optional[logging.Logger] = None):
        self.response_json = copy.deepcopy(source_json or {"choices": [{"message": {}}]})
        self.response_json["model"] = custom_model_id
        self.logger = logger
        choices = self.response_json.setdefault("choices", [{}])
        if not choices:
            choices.append({})
        self.choice = choices[0]
        self.message = self.choice.setdefault("message", {})
        self.role = self.message.get("role", "assistant")
        self.content = ""
        self.reasoning_content = ""
        self.tool_calls_by_index: dict[int, dict] = {}
        self.finish_reason = self.choice.get("finish_reason")

        self.message.pop("reasoning", None)
        self.message.pop("reasoning_details", None)
        self.message.pop("reasoning_content", None)
        self.message.pop("tool_calls", None)
        self.message["content"] = ""

    def push_chunk(self, chunk: dict) -> None:
        """消费一个已经过 converter 清洗的 chat chunk。"""
        if "id" in chunk:
            self.response_json["id"] = chunk["id"]
        if "created" in chunk:
            self.response_json["created"] = chunk["created"]

        chunk_choices = chunk.get("choices", [])
        if not chunk_choices:
            return
        delta = chunk_choices[0].get("delta", {})
        self.finish_reason = chunk_choices[0].get("finish_reason") or self.finish_reason

        if "role" in delta:
            self.role = delta["role"]
        if "content" in delta:
            self.content += delta.get("content") or ""
        if "reasoning_content" in delta:
            self.reasoning_content += delta.get("reasoning_content") or ""
        for tool_call in delta.get("tool_calls", []):
            tool_index = tool_call.get("index")
            if not isinstance(tool_index, int):
                if self.logger is not None:
                    self.logger.warning(f"跳过缺少 index 的 tool_call chunk: {tool_call}")
                continue
            aggregated = self.tool_calls_by_index.setdefault(tool_index, {
                "id": "",
                "type": "function",
                "function": {
                    "name": "",
                    "arguments": "",
                },
            })
            if tool_call.get("id"):
                aggregated["id"] = tool_call["id"]
            if tool_call.get("type"):
                aggregated["type"] = tool_call["type"]
            function = tool_call.get("function", {})
            if function.get("name"):
                aggregated["function"]["name"] = function["name"]
            aggregated["function"]["arguments"] += function.get("arguments", "")

    def build(self) -> dict:
        """输出最终的非流式 chat/completions JSON。"""
        self.message["role"] = self.role
        self.message["content"] = self.content
        if self.reasoning_content:
            self.message["reasoning_content"] = self.reasoning_content
        if self.tool_calls_by_index:
            self.message["tool_calls"] = [
                self.tool_calls_by_index[index]
                for index in sorted(self.tool_calls_by_index)
            ]
        self.choice["finish_reason"] = self.finish_reason
        return self.response_json


class CompletionResponseAssembler:
    """聚合 completions 事件流。"""

    def __init__(self, custom_model_id: str, source_json: Optional[dict]):
        self.response_json = copy.deepcopy(source_json or {"choices": [{}]})
        self.response_json["model"] = custom_model_id
        choices = self.response_json.setdefault("choices", [{}])
        if not choices:
            choices.append({})
        self.choice = choices[0]
        self.text = ""
        self.finish_reason = self.choice.get("finish_reason")
        self.choice["text"] = ""

    def push_chunk(self, chunk: dict) -> None:
        """消费一个已经过 converter 清洗的 completion chunk。"""
        if "id" in chunk:
            self.response_json["id"] = chunk["id"]
        if "created" in chunk:
            self.response_json["created"] = chunk["created"]

        chunk_choices = chunk.get("choices", [])
        if not chunk_choices:
            return
        self.text += chunk_choices[0].get("text", "")
        self.finish_reason = chunk_choices[0].get("finish_reason") or self.finish_reason

    def build(self) -> dict:
        """输出最终的非流式 completions JSON。"""
        self.choice["text"] = self.text
        self.choice["finish_reason"] = self.finish_reason
        return self.response_json


async def assemble_chat_completion_response(
    upstream_events,
    converter,
    custom_model_id: str,
    source_json: Optional[dict],
    logger: Optional[logging.Logger] = None,
) -> dict:
    """从统一事件流构建 chat/completions 非流式响应。"""
    assembler = ChatCompletionResponseAssembler(custom_model_id, source_json, logger=logger)
    async for upstream_event in upstream_events:
        if not isinstance(upstream_event, UpstreamSSEEvent):
            continue
        chunk = parse_processed_chunk(upstream_event, converter, logger=logger)
        if chunk is None:
            continue
        assembler.push_chunk(chunk)
    return assembler.build()


async def assemble_completion_response(
    upstream_events,
    converter,
    custom_model_id: str,
    source_json: Optional[dict],
    logger: Optional[logging.Logger] = None,
) -> dict:
    """从统一事件流构建 completions 非流式响应。"""
    assembler = CompletionResponseAssembler(custom_model_id, source_json)
    async for upstream_event in upstream_events:
        if not isinstance(upstream_event, UpstreamSSEEvent):
            continue
        chunk = parse_processed_chunk(upstream_event, converter, logger=logger)
        if chunk is None:
            continue
        assembler.push_chunk(chunk)
    return assembler.build()
