"""LLM-Proxy 主入口 - 简化为配置加载和服务启动"""
import sys
import logging
from contextlib import asynccontextmanager
import httpx
import uvicorn
from fastapi import FastAPI

from config import ConfigLoader
from proxy import ProxyHandler, ChunkConverterMatcher, RecordingMiddleware, ProxyTransport, TransportRecordingMiddleware, ReplayMiddleware
from routes import register_routes

logger = logging.getLogger('llm_proxy')

proxy_handler: ProxyHandler
config = None


@asynccontextmanager
async def lifespan(app: FastAPI):
    """应用生命周期管理"""
    global proxy_handler, config

    # 设置连接池限制以提高高并发情况下的网络处理能力
    limits = httpx.Limits(max_keepalive_connections=200, max_connections=2000)
    # 分段超时：连接快速失败，读取留足够时间给 Agent 长推理任务
    timeout = httpx.Timeout(connect=10.0, read=600.0, write=30.0, pool=30.0)

    # 构建中间件列表
    middlewares = []
    if config.recording.enabled:
        from pathlib import Path
        middlewares.append(ReplayMiddleware(
            recordings_dir=Path("recordings"),
            logger=logger
        ))
        middlewares.append(TransportRecordingMiddleware(logger=logger))

    transport = ProxyTransport(logger=logger, middlewares=middlewares)
    client = httpx.AsyncClient(timeout=timeout, limits=limits, transport=transport)

    await proxy_handler.set_client(client)
    logger.info("初始化 httpx.AsyncClient 成功")

    yield

    await client.aclose()
    logger.info("关闭 httpx.AsyncClient")


def main() -> None:
    """主函数 - 简化为配置加载和服务启动"""
    global config
    try:
        config = ConfigLoader().load()
    except (FileNotFoundError, ValueError, RuntimeError) as e:
        print(f"错误: 配置加载失败 - {e}", file=sys.stderr)
        sys.exit(1)

    log_level = logging.DEBUG if config.server.debug else logging.INFO
    # 清理可能已经存在的处理器，防止重复打印
    logging.getLogger().handlers.clear()

    # 移除所有 uvicorn 相关的 logger 的 handler
    for name in logging.root.manager.loggerDict:
        if name.startswith('uvicorn'):
            logging.getLogger(name).handlers.clear()
            logging.getLogger(name).propagate = True

    logging.basicConfig(
        level=log_level,
        format='%(asctime)s [%(levelname)s] %(name)s - %(message)s',
        datefmt='%Y-%m-%d %H:%M:%S',
        force=True
    )

    logger.info("正在启动 LLM-Proxy...")

    parser_matcher = ChunkConverterMatcher(config.chunk_parsers, logger=logger)

    global proxy_handler
    proxy_handler = ProxyHandler(
        backends=config.backends,
        logger=logger,
        parser_matcher=parser_matcher,
        sse_coalescing_config=config.sse_coalescing,
    )

    app = FastAPI(title="LLM-Proxy", lifespan=lifespan)

    # 添加录制中间件
    if config.recording.enabled:
        app.add_middleware(RecordingMiddleware, config=config, logger=logger)

    register_routes(app, handler=proxy_handler)

    for api in config.backends.apis:
        logger.info(f"  - API: {api.name} ({api.custom_model_id} -> {api.target_model_id})")

    for group in config.backends.groups:
        logger.info(f"  - Group: {group.name} (prefix: {group.model_prefix})")

    logger.info(f"调试模式: {'开启' if config.server.debug else '关闭'}")

    uvicorn_kwargs = {
        "host": "0.0.0.0",
        "port": config.server.port,
        "log_config": None, # 禁用 Uvicorn 默认日志配置，统一使用根日志配置
    }
    if config.server.key_file and config.server.cert_file:
        uvicorn_kwargs["ssl_keyfile"] = config.server.key_file
        uvicorn_kwargs["ssl_certfile"] = config.server.cert_file

    uvicorn.run(app, **uvicorn_kwargs)


if __name__ == "__main__":
    main()
