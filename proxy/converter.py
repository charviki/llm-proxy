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
    """默认 Chunk 转换器 - 兜底透传，不对数据做任何解析"""

    def __init__(self, model_id: str):
        self.model_id = model_id

    def parse(self, data_str: str) -> Optional[str]:
        """
        纯透传，直接返回原始字符串
        """
        return data_str

class ThinkState(IntEnum):
    UNSTARTED = 0
    THINKING = 1
    FINISHED = 2

class AbstractReasoningConverter(BaseChunkConverter, ABC):
    """思考内容转换器抽象基类 - 封装完整的解析与极速透传逻辑"""

    def __init__(self, model_id: str):
        super().__init__(model_id)
        self.think_state = ThinkState.UNSTARTED

    def parse(self, data_str: str) -> Optional[str]:
        """
        处理原始 data 字符串，返回处理后的 data 字符串。
        如果返回 None，表示该 chunk 为空，应被丢弃。
        """
        # 如果思考阶段已结束，直接极速透传
        if self.think_state == ThinkState.FINISHED:
            return data_str

        try:
            data = json.loads(data_str)
            choices = data.get("choices", [])
            if choices and len(choices) > 0:
                delta = choices[0].get("delta", {})
                
                result = self.process_chunk(delta)
                
                has_reasoning = bool(result.reasoning)
                has_content = bool(result.content)
                
                if has_reasoning:
                    delta["reasoning_content"] = result.reasoning
                    
                if has_content:
                    delta["content"] = result.content
                
                # 防御性清理私有字段
                delta.pop("reasoning", None)
                delta.pop("reasoning_details", None)
                
                if not delta:
                    return None
                    
                return json.dumps(data, ensure_ascii=False, separators=(',', ':'))
        except Exception:
            # 针对解析异常，或者其它无法预料的错误，安全回退为返回原字符串
            pass
            
        return data_str

    @abstractmethod
    def process_chunk(self, delta: dict) -> ReasoningContent:
        """处理单个 delta chunk，提取思考和正常内容
        
        Args:
            delta: API 返回的 delta 数据

        Returns:
            ReasoningContent: 分离后的思考内容和正常内容
        """
        pass


class ThinkTagChunkConverter(AbstractReasoningConverter):
    """ThinkTag 转换器 - 处理 <think> 和 </think> 标签

    用于 Claude、MiniMax 等在 content 中使用标签包裹思考内容的模型
    不再处理跨 chunk 的截断，直接用简单的 split 分割
    """

    def __init__(self, model_id: str):
        super().__init__(model_id)

    def process_chunk(self, delta: dict) -> ReasoningContent:
        """处理 delta，提取 <think> 和 </think> 标签内容"""
        content = delta.pop("content", None) or ""
        
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
