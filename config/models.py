"""配置模型定义 - 使用 Pydantic 进行类型安全配置管理"""
from pydantic import BaseModel, Field
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


class AppConfig(BaseModel):
    """应用完整配置"""
    domains: list[str] = Field(
        default_factory=lambda: ["api.openai.com"],
        description="SSL 证书域名列表"
    )
    chunk_parsers: dict[str, str] = Field(
        default_factory=lambda: {"default": "reasoning_content"},
        description="模型关键词到解析器的映射"
    )
    backends: BackendsConfig = Field(default_factory=BackendsConfig, description="后端配置")
    server: ServerConfig = Field(..., description="服务器配置")
