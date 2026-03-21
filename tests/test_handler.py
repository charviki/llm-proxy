import pytest
from unittest.mock import AsyncMock, MagicMock
from fastapi import Request
import json
import logging
from proxy.handler import ProxyHandler
from proxy.converter import ChunkConverterMatcher
from config.models import BackendsConfig, APIConfig

@pytest.fixture
def mock_logger():
    return logging.getLogger("test_logger")

@pytest.fixture
def mock_parser_matcher():
    matcher = ChunkConverterMatcher({"default": "reasoning_content"})
    return matcher

@pytest.fixture
def backends_config():
    return BackendsConfig(
        groups=[],
        apis=[
            APIConfig(
                name="Test API",
                endpoint="https://api.test.com",
                stream=True,
                custom_model_id="my-model",
                target_model_id="real-model"
            )
        ]
    )

@pytest.mark.asyncio
async def test_handle_completions_invalid_json(backends_config, mock_logger, mock_parser_matcher):
    handler = ProxyHandler(backends_config, mock_logger, mock_parser_matcher)
    
    request = AsyncMock(spec=Request)
    request.headers = {"Content-Type": "application/json"}
    request.json = AsyncMock(side_effect=json.JSONDecodeError("Expecting value", "", 0))
    
    response = await handler.handle_chat_completions(request)
    assert response.status_code == 400
    res_content = json.loads(response.body)
    assert "JSON解析失败" in res_content["error"]

@pytest.mark.asyncio
async def test_handle_chat_completions_model_not_found(backends_config, mock_logger, mock_parser_matcher):
    handler = ProxyHandler(backends_config, mock_logger, mock_parser_matcher)
    
    # Needs a mock client to initialize models_manager
    await handler.set_client(AsyncMock())
    
    request = AsyncMock(spec=Request)
    request.headers = {"Content-Type": "application/json"}
    request.json = AsyncMock(return_value={"model": "unknown-model"})
    
    response = await handler.handle_chat_completions(request)
    assert response.status_code == 400
    res_content = json.loads(response.body)
    assert "未找到匹配的模型" in res_content["error"]

@pytest.mark.asyncio
async def test_handle_chat_completions_non_stream(backends_config, mock_logger, mock_parser_matcher):
    handler = ProxyHandler(backends_config, mock_logger, mock_parser_matcher)
    
    mock_client = AsyncMock()
    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.json.return_value = {"id": "123", "choices": [{"message": {"content": "Hello"}}], "model": "real-model"}
    mock_client.post.return_value = mock_response
    
    await handler.set_client(mock_client)
    
    request = AsyncMock(spec=Request)
    request.headers = {"Content-Type": "application/json"}
    request.json = AsyncMock(return_value={"model": "my-model", "messages": []})
    
    response = await handler.handle_chat_completions(request)
    assert response.status_code == 200
    res_content = json.loads(response.body)
    assert res_content["model"] == "my-model"
    assert res_content["choices"][0]["message"]["content"] == "Hello"

@pytest.mark.asyncio
async def test_handle_chat_completions_stream_simulation(backends_config, mock_logger, mock_parser_matcher):
    # APIConfig says stream=False (not supported by backend)
    backends_config.apis[0].stream = False
    handler = ProxyHandler(backends_config, mock_logger, mock_parser_matcher)
    
    mock_client = AsyncMock()
    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.json.return_value = {
        "id": "123", 
        "choices": [{"message": {"content": "Simulated"}}]
    }
    mock_client.post.return_value = mock_response
    
    await handler.set_client(mock_client)
    
    request = AsyncMock(spec=Request)
    request.headers = {"Content-Type": "application/json"}
    # Client requests stream=True
    request.json = AsyncMock(return_value={"model": "my-model", "messages": [], "stream": True})
    
    response = await handler.handle_chat_completions(request)
    
    # Backend was called with stream=False
    called_json = mock_client.post.call_args[1]["json"]
    assert called_json["stream"] is False
    
    # Response should be a StreamingResponse
    assert response.media_type == "text/event-stream"
