"""思考内容转换器 - 可扩展架构，支持不同模型的思考内容格式"""
import json
import logging
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

    def __init__(self, model_id: str, logger: logging.Logger):
        self.model_id = model_id
        self.logger = logger

    def parse(self, data_str: str) -> Optional[str]:
        """
        透传，仅替换 model ID 为客户端请求的自定义模型名
        """
        try:
            data = json.loads(data_str)
            if "model" in data:
                data["model"] = self.model_id
            return json.dumps(data, ensure_ascii=False, separators=(',', ':'))
        except json.JSONDecodeError:
            return data_str
        except Exception as e:
            self.logger.exception(f"BaseChunkConverter 解析失败: {e}")
            return data_str

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

    def __init__(self, model_id: str, logger: logging.Logger):
        super().__init__(model_id, logger)
        self.think_state = ThinkState.UNSTARTED

    def parse(self, data_str: str) -> Optional[str]:
        """
        处理原始 data 字符串，返回处理后的 data 字符串。
        如果返回 None，表示该 chunk 为空，应被丢弃。
        """
        # 一旦确认后续不再有思考字段，走极速透传路径，避免每个 chunk 都重复做提取逻辑。
        if self.think_state == ThinkState.FINISHED:
            try:
                data = json.loads(data_str)
                if "model" in data:
                    data["model"] = self.model_id
                return json.dumps(data, ensure_ascii=False, separators=(',', ':'))
            except json.JSONDecodeError:
                return data_str
            except Exception as e:
                self.logger.exception(f"AbstractReasoningConverter 快速透传解析失败: {e}")
                return data_str

        try:
            data = json.loads(data_str)

            # model ID 回映射
            if "model" in data:
                data["model"] = self.model_id

            choices = data.get("choices", [])
            if choices and len(choices) > 0:
                delta = choices[0].get("delta", {})

                # process_chunk 会直接读取并复用现有 delta，避免额外构造中间对象。
                result = self.process_chunk(delta)

                # 上游私有字段统一在这里抹平，后续流式/非流式消费者只感知标准字段。
                delta.pop("reasoning", None)
                delta.pop("reasoning_details", None)

                # 只有存在思考文本时才补 reasoning_content，避免制造空字符串增量。
                if result.reasoning:
                    delta["reasoning_content"] = result.reasoning

                # 对 think_tag / reasoning 这类“从 content 中剥离思考”的场景，
                # 如果正文已经被完整消费掉，必须把原始 content 删掉，避免客户端看到重复文本。
                if "content" in delta:
                    if result.content:
                        delta["content"] = result.content
                    else:
                        delta.pop("content", None)

                # 某些纯思考 chunk 在标准化后可能什么都不剩，这类 chunk 应直接丢弃。
                if not delta:
                    return None

            return json.dumps(data, ensure_ascii=False, separators=(',', ':'))
        except json.JSONDecodeError:
            return data_str
        except Exception as e:
            self.logger.exception(f"AbstractReasoningConverter 解析失败: {e}")
            return data_str

    @abstractmethod
    def process_chunk(self, delta: dict) -> ReasoningContent:
        """处理单个 delta chunk，提取思考和正常内容"""
        pass


class ThinkTagChunkConverter(AbstractReasoningConverter):
    """Think Tag 转换器 - 处理 <think> 和 </think> 标签包裹的思考内容

    用于 Claude、MiniMax 等在 content 中使用标签包裹思考内容的模型
    """

    def __init__(self, model_id: str, logger: logging.Logger):
        super().__init__(model_id, logger)

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
                # 结束标签和正文可能落在同一个 chunk 里，需要一次性拆开。
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

                # 有些模型会把 <think>...</think> 和正文一次性放进同一个 chunk。
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


class ReasoningChunkConverter(AbstractReasoningConverter):
    """通用 reasoning 字段转换器 - 处理 reasoning 和 reasoning_details 字段"""

    def __init__(self, model_id: str, logger: logging.Logger):
        super().__init__(model_id, logger)

    def process_chunk(self, delta: dict) -> ReasoningContent:
        """处理 delta，直接提取 reasoning 字段"""
        reasoning = delta.pop("reasoning", None) or ""
        content = delta.get("content") or ""

        # reasoning 模型一旦开始输出正文，后面通常不再返回思考字段，可以切到极速透传路径。
        if content:
            self.think_state = ThinkState.FINISHED

        return ReasoningContent(reasoning=reasoning, content=content)


ReasoningFieldChunkConverter = ReasoningChunkConverter
GeminiChunkConverter = ReasoningChunkConverter


class ReasoningContentChunkConverter(AbstractReasoningConverter):
    """通用 Reasoning Content 转换器 - 处理 reasoning_content 字段

    用于 DeepSeek 等使用 reasoning_content 字段的模型
    """

    def __init__(self, model_id: str, logger: logging.Logger):
        super().__init__(model_id, logger)

    def process_chunk(self, delta: dict) -> ReasoningContent:
        """提取 reasoning_content"""
        reasoning = delta.pop("reasoning_content", None) or ""
        content = delta.get("content") or ""

        # reasoning_content 模型与 reasoning 模型一样，正文出现后后续 chunk 通常无需再做字段标准化。
        if content:
            self.think_state = ThinkState.FINISHED

        return ReasoningContent(reasoning=reasoning, content=content)


def create_parser(parser_type: str, model_id: str, logger: logging.Logger) -> BaseChunkConverter:
    """根据解析器类型创建解析器实例"""
    if parser_type == "think_tag":
        return ThinkTagChunkConverter(model_id, logger)
    elif parser_type == "reasoning":
        return ReasoningChunkConverter(model_id, logger)
    else:
        return ReasoningContentChunkConverter(model_id, logger)


class ChunkConverterMatcher:
    """解析器匹配器 - 根据模型名称关键词匹配解析器"""

    def __init__(self, parser_config: dict[str, str], logger: logging.Logger):
        self.parser_config = parser_config
        self.logger = logger

    def get_parser(self, model_id: str) -> BaseChunkConverter:
        """根据模型 ID 获取合适的解析器，默认返回 BaseChunkConverter 作为兜底"""
        model_lower = model_id.lower()

        for keyword, parser_type in self.parser_config.items():
            if keyword != "default" and keyword in model_lower:
                return create_parser(parser_type, model_id, self.logger)

        return BaseChunkConverter(model_id, self.logger)
