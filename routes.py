"""路由注册模块 - 统一管理所有 API 路由"""
from fastapi import FastAPI, Request

from proxy.handler import ProxyHandler


def register_routes(app: FastAPI, handler: ProxyHandler) -> None:
    """注册所有路由"""

    @app.get("/")
    async def root():
        """处理根路径请求"""
        return {
            "message": "Welcome to the API!"
        }

    @app.get("/v1")
    async def v1_root():
        """处理 /v1 路径请求"""
        return {
            "message": "API v1 endpoint",
            "endpoints": {
                "chat/completions": "/v1/chat/completions",
                "completions": "/v1/completions"
            }
        }

    @app.get("/v1/models")
    async def list_models():
        """列出可用模型（标准 OpenAI 格式）"""
        if handler.models_manager:
            return handler.models_manager.get_all_models()
        return {"object": "list", "data": []}

    @app.post("/v1/chat/completions")
    async def chat_completions(request: Request):
        """处理聊天完成请求"""
        return await handler.handle_chat_completions(request)

    @app.post("/v1/completions")
    async def completions(request: Request):
        """处理文本补全请求"""
        return await handler.handle_completions(request)
