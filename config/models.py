"""配置模型定义 - 使用 Pydantic 进行类型安全配置管理"""
from pydantic import BaseModel, Field, field_validator
from typing import Optional


class GroupConfig(BaseModel):
    """组配置"""
    name: str = Field(..., description="组名称")
    model_prefix: str = Field(..., description="模型前缀")
    endpoint: str = Field(..., description="API 端点")
    stream: bool = Field(True, description="该组后端是否原生支持流式输出")
    models_endpoint: Optional[str] = Field(None, description="模型列表接口")
    models_file: Optional[str] = Field(None, description="模型列表文件路径")
    api_key_env: Optional[str] = Field(None, description="API Key 环境变量名")


class APIConfig(BaseModel):
    """API 配置"""
    name: str = Field(..., description="API 名称")
    endpoint: str = Field(..., description="API 端点")
    stream: bool = Field(True, description="该 API 后端是否原生支持流式输出")
    custom_model_id: str = Field(..., description="精确匹配的模型 ID")
    target_model_id: str = Field(..., description="目标模型 ID")
    api_key_env: Optional[str] = Field(None, description="API Key 环境变量名")


class BackendsConfig(BaseModel):
    """后端配置"""
    groups: list[GroupConfig] = Field(default_factory=list, description="组配置列表")
    apis: list[APIConfig] = Field(default_factory=list, description="API 配置列表")


class ServerConfig(BaseModel):
    """服务器配置"""
    domains: list[str] = Field(
        default_factory=lambda: ["api.openai.com"],
        description="SSL 证书域名列表"
    )
    port: int = Field(443, description="监听端口")
    debug: bool = Field(False, description="调试模式")
    cert_file: Optional[str] = Field(None, description="SSL 证书路径")
    key_file: Optional[str] = Field(None, description="SSL 私钥路径")


class RecordingConfig(BaseModel):
    """流量录制配置"""
    enabled: bool = Field(False, description="是否启用流量录制")
    record_paths: list[str] = Field(
        default_factory=lambda: ["/v1/chat/completions"],
        description="需要录制的请求路径列表"
    )


class SSECoalescingConfig(BaseModel):
    """SSE 语义合包配置"""
    enabled: bool = Field(False, description="是否启用 SSE 语义合包")
    window_ms: int = Field(20, ge=1, description="语义合包时间窗口（毫秒）")
    max_buffer_length: int = Field(256, ge=1, description="语义合包长度阈值（字符数）")


class AppConfig(BaseModel):
    """应用完整配置"""
    domains: list[str] = Field(
        default_factory=lambda: ["api.openai.com"],
        description="SSL 证书域名列表"
    )
    chunk_parsers: dict[str, str | list[str]] = Field(
        default_factory=dict,
        description="Chunk 解析器配置，推荐使用 parser -> keywords 的映射结构"
    )
    backends: BackendsConfig = Field(default_factory=BackendsConfig, description="后端配置")
    server: ServerConfig = Field(..., description="服务器配置")
    recording: RecordingConfig = Field(default_factory=lambda: RecordingConfig(enabled=False), description="流量录制配置")
    sse_coalescing: SSECoalescingConfig = Field(default_factory=SSECoalescingConfig, description="SSE 语义合包配置")

    @field_validator("chunk_parsers", mode="before")
    @classmethod
    def normalize_chunk_parsers(cls, value):
        from proxy.converter import get_supported_chunk_parser_types

        if value is None:
            return {}
        if not isinstance(value, dict):
            raise TypeError("chunk_parsers 必须是对象映射")

        supported_parser_types = get_supported_chunk_parser_types()
        normalized: dict[str, str | list[str]] = {}

        for key, raw_value in value.items():
            if key == "default":
                if not isinstance(raw_value, str):
                    raise TypeError("chunk_parsers.default 必须是字符串")
                if raw_value not in supported_parser_types:
                    raise ValueError(
                        f"chunk_parsers.default 使用了不支持的解析器 {raw_value}，"
                        f"当前支持: {', '.join(sorted(supported_parser_types))}"
                    )
                normalized[key] = raw_value
                continue

            if key not in supported_parser_types:
                raise ValueError(
                    f"chunk_parsers.{key} 不是支持的解析器名称，"
                    f"请使用 parser -> keywords 结构，当前支持: {', '.join(sorted(supported_parser_types))}"
                )

            keywords = cls._normalize_keywords(raw_value, parser_type=key)
            if keywords:
                normalized[key] = keywords

        return normalized

    @staticmethod
    def _normalize_keywords(raw_value: str | list[str], parser_type: str) -> list[str]:
        keywords = [raw_value] if isinstance(raw_value, str) else raw_value
        if not isinstance(keywords, list) or not all(isinstance(keyword, str) for keyword in keywords):
            raise TypeError(f"chunk_parsers.{parser_type} 必须是字符串或字符串列表")
        return keywords
