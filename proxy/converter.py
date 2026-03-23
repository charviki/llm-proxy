"""思考内容转换器 - 可扩展架构，支持不同模型的思考内容格式"""
import json
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Optional
from enum import IntEnum


@dataclass
class ReasoningContent:
    """思考内容数据结构"""
    reasoning: Optional[str] = None  # 思考内容
    content: Optional[str] = None   # 正常内容


class BaseChunkConverter:
    """默认 Chunk 转换器 - 兜底透传，仅做 model ID 回映射"""

    def __init__(self, model_id: str):
        self.model_id = model_id

    def parse(self, data_str: str) -> Optional[str]:
        """
        透传，仅替换 model ID 为客户端请求的自定义模型名
        """
        try:
            data = json.loads(data_str)
            if "model" in data:
                data["model"] = self.model_id
            return json.dumps(data, ensure_ascii=False, separators=(',', ':'))
        except Exception:
            return data_str

    def process_chunk(self, delta: dict) -> ReasoningContent:
        """
        兜底处理，直接返回原始 content，不提取 reasoning
        流式和非流式请求均使用此接口
        """
        content = delta.get("content", None)
        return ReasoningContent(reasoning=None, content=content)


    def process_chunk(self, delta: dict) -> ReasoningContent:
        """
        兜底处理，直接返回原始 content，不提取 reasoning
        为非流式请求提供兼容的接口调用
        """
        content = delta.get("content", None)
        return ReasoningContent(reasoning=None, content=content)

class ThinkState(IntEnum):
    UNSTARTED = 0
    THINKING = 1
    FINISHED = 2


class AbstractReasoningConverter(BaseChunkConverter, ABC):
    """思考内容转换器抽象基类 - 原地修改 delta，提取思考内容"""

    def __init__(self, model_id: str):
        super().__init__(model_id)
        self.think_state = ThinkState.UNSTARTED

    def parse(self, data_str: str) -> Optional[str]:
        """
        处理原始 data 字符串，返回处理后的 data 字符串。
        如果返回 None，表示该 chunk 为空，应被丢弃。
        """
        # 如果思考阶段已结束，仅做 model ID 回映射后透传
        if self.think_state == ThinkState.FINISHED:
            try:
                data = json.loads(data_str)
                if "model" in data:
                    data["model"] = self.model_id
                return json.dumps(data, ensure_ascii=False, separators=(',', ':'))
            except Exception:
                return data_str

        try:
            data = json.loads(data_str)

            # model ID 回映射
            if "model" in data:
                data["model"] = self.model_id

            choices = data.get("choices", [])
            if choices and len(choices) > 0:
                delta = choices[0].get("delta", {})

                # 原地提取思考内容到 reasoning_content
                result = self.process_chunk(delta)

                # 清理私有字段
                delta.pop("reasoning", None)
                delta.pop("reasoning_details", None)

                # 提取出 thinking 后设置标准字段
                if result.reasoning:
                    delta["reasoning_content"] = result.reasoning

                # 保留处理后的 content
                if result.content:
                    delta["content"] = result.content

                # 处理后 delta 为空则丢弃
                if not delta:
                    return None

            return json.dumps(data, ensure_ascii=False, separators=(',', ':'))
        except Exception:
            return data_str

    @abstractmethod
    def process_chunk(self, delta: dict) -> ReasoningContent:
        """处理单个 delta chunk，提取思考和正常内容"""
        pass


class ThinkTagChunkConverter(AbstractReasoningConverter):
    """Think Tag 转换器 - 处理 <think> 和 </think> 标签包裹的思考内容

    用于 Claude、MiniMax 等在 content 中使用标签包裹思考内容的模型
    """

    def __init__(self, model_id: str):
        super().__init__(model_id)

    def process_chunk(self, delta: dict) -> ReasoningContent:
        """处理 delta，提取 <think> 和 </think> 标签内容"""
        content = delta.get("content") or ""

        if not content:
            return ReasoningContent(reasoning="", content="")

        # 防御性：如果状态已是 FINISHED（理论上会被 parse 的快速透传拦截，不会进到这里），直接返回正文
        if self.think_state == ThinkState.FINISHED:
            return ReasoningContent(reasoning="", content=content)

        reasoning_content = ""
        normal_content = ""

        if self.think_state == ThinkState.THINKING:
            if "</think>" in content:
                # 思考结束，分离残留思考与正文
                parts = content.split("</think>", 1)
                reasoning_content = parts[0]
                normal_content = parts[1]

                self.think_state = ThinkState.FINISHED
            else:
                # 纯思考内容，没有遇到结束标签
                reasoning_content = content

        elif self.think_state == ThinkState.UNSTARTED:
            if "<think>" in content:
                self.think_state = ThinkState.THINKING
                parts = content.split("<think>", 1)
                normal_content = parts[0]

                # 检查是否在同一个 chunk 内直接结束了思考（非常罕见但可能发生）
                if "</think>" in parts[1]:
                    sub_parts = parts[1].split("</think>", 1)
                    reasoning_content = sub_parts[0]
                    normal_content += sub_parts[1]

                    self.think_state = ThinkState.FINISHED
                else:
                    reasoning_content = parts[1]
            else:
                # 纯正文内容，说明没有思考过程，直接标记思考结束，开启极速透传
                normal_content = content
                self.think_state = ThinkState.FINISHED

        return ReasoningContent(reasoning=reasoning_content, content=normal_content)


class GeminiChunkConverter(AbstractReasoningConverter):
    """Google Gemini 思考转换器 - 处理 reasoning 和 reasoning_details 字段"""

    def __init__(self, model_id: str):
        super().__init__(model_id)

    def process_chunk(self, delta: dict) -> ReasoningContent:
        """处理 delta，直接提取 reasoning 字段"""
        reasoning = delta.pop("reasoning", None) or ""
        content = delta.get("content") or ""

        if content:
            self.think_state = ThinkState.FINISHED

        return ReasoningContent(reasoning=reasoning, content=content)


class ReasoningContentChunkConverter(AbstractReasoningConverter):
    """通用 Reasoning Content 转换器 - 处理 reasoning_content 字段

    用于 DeepSeek 等使用 reasoning_content 字段的模型
    """

    def __init__(self, model_id: str):
        super().__init__(model_id)

    def process_chunk(self, delta: dict) -> ReasoningContent:
        """提取 reasoning_content"""
        reasoning = delta.pop("reasoning_content", None) or ""
        content = delta.get("content") or ""

        if content:
            self.think_state = ThinkState.FINISHED

        return ReasoningContent(reasoning=reasoning, content=content)


def create_parser(parser_type: str, model_id: str) -> BaseChunkConverter:
    """根据解析器类型创建解析器实例"""
    if parser_type == "think_tag":
        return ThinkTagChunkConverter(model_id)
    elif parser_type == "reasoning":
        return GeminiChunkConverter(model_id)
    else:
        return ReasoningContentChunkConverter(model_id)


class ChunkConverterMatcher:
    """解析器匹配器 - 根据模型名称关键词匹配解析器"""

    def __init__(self, parser_config: dict[str, str]):
        self.parser_config = parser_config

    def get_parser(self, model_id: str) -> BaseChunkConverter:
        """根据模型 ID 获取合适的解析器，默认返回 BaseChunkConverter 作为兜底"""
        model_lower = model_id.lower()

        for keyword, parser_type in self.parser_config.items():
            if keyword != "default" and keyword in model_lower:
                return create_parser(parser_type, model_id)

        return BaseChunkConverter(model_id)
