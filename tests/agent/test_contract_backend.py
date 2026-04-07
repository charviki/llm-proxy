import json
import logging
import os
from pathlib import Path
from unittest.mock import AsyncMock

import httpx
import pytest
import yaml
from fastapi import Request
from fastapi.responses import JSONResponse
from starlette.datastructures import Headers

from config.models import APIConfig, BackendsConfig
from proxy.converter import ChunkConverterMatcher
from proxy.handler import ProxyHandler
from tests.helpers.response_parsing import collect_chunks_from_sse_body, parse_nonstream_body, parse_sse_chunks

CONTRACT_CONFIG_ENV = "LLM_PROXY_CONTRACT_CONFIG"
ENABLE_CONTRACT_TESTS_ENV = "ENABLE_REAL_BACKEND_CONTRACT_TESTS"


def _load_contract_config() -> dict:
    if os.environ.get(ENABLE_CONTRACT_TESTS_ENV) != "1":
        pytest.skip("未启用真实 backend 契约测试")

    config_path = Path(os.environ.get(CONTRACT_CONFIG_ENV, Path(__file__).with_name("contract-config.yml")))
    if not config_path.exists():
        pytest.skip(f"未找到契约测试配置文件: {config_path}")

    with open(config_path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f)


def _build_request(model: str, messages: list[dict], stream: bool) -> Request:
    request = AsyncMock(spec=Request)
    request.headers = Headers({"content-type": "application/json"})
    request.json = AsyncMock(return_value={"model": model, "messages": messages, "stream": stream})
    return request


async def _collect_stream_response(response) -> dict:
    body_chunks = []
    async for chunk in response.body_iterator:
        if isinstance(chunk, str):
            chunk = chunk.encode("utf-8")
        body_chunks.append(chunk)
    return parse_sse_chunks(collect_chunks_from_sse_body(b"".join(body_chunks)))


@pytest.mark.asyncio
@pytest.mark.contract
@pytest.mark.parametrize("request_stream", [True, False])
async def test_real_backend_contract(request_stream):
    config = _load_contract_config()
    backend_config = config["backend"]
    parser_config = config["parser"]
    request_config = config["request"]

    if request_stream not in request_config.get("stream_cases", [True, False]):
        pytest.skip(f"配置未启用 stream={request_stream} 的契约测试")

    if not os.environ.get(backend_config["api_key_env"]):
        pytest.skip(f"缺少 API Key 环境变量: {backend_config['api_key_env']}")

    matcher = ChunkConverterMatcher(
        {parser_config["type"]: parser_config.get("keywords", []), "default": parser_config["type"]},
        logger=logging.getLogger("test_contract_backend"),
    )
    handler = ProxyHandler(
        backends=BackendsConfig(
            groups=[],
            apis=[
                APIConfig(
                    name="Contract Backend",
                    endpoint=backend_config["endpoint"],
                    stream=backend_config.get("stream", True),
                    custom_model_id=backend_config["custom_model_id"],
                    target_model_id=backend_config["target_model_id"],
                    api_key_env=backend_config["api_key_env"],
                )
            ],
        ),
        logger=logging.getLogger("test_contract_backend"),
        parser_matcher=matcher,
    )

    async with httpx.AsyncClient(timeout=120.0) as client:
        await handler.set_client(client)
        response = await handler.handle_chat_completions(
            _build_request(
                backend_config["custom_model_id"],
                request_config["messages"],
                request_stream,
            )
        )

    assert response.status_code == 200
    if request_stream:
        parsed = await _collect_stream_response(response)
        assert parsed["done_seen"] is True
    else:
        assert isinstance(response, JSONResponse)
        parsed = parse_nonstream_body(json.loads(response.body))

    assert parsed["content"] or parsed["reasoning_content"] or parsed["tool_calls"]
