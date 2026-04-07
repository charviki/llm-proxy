"""
Agent Workflow 集成测试

验证 llm-proxy 的组装逻辑在代码改动后不受影响：
- Reasoning Content 完整性
- Tool Calls 字段不丢失
- 流式与非流式路径都能正确工作
- 真实录制样本作为硬门禁回归资产
"""

import json
import logging
from pathlib import Path
from unittest.mock import AsyncMock

import httpx
import pytest
from fastapi import Request
from fastapi.responses import JSONResponse
from starlette.datastructures import Headers

from config.models import APIConfig, BackendsConfig
from proxy.converter import ChunkConverterMatcher
from proxy.handler import ProxyHandler
from tests.fixtures.mock_server import create_mock_server
from tests.helpers.response_parsing import collect_chunks_from_sse_body, normalize_text, parse_nonstream_body, parse_sse_chunks

REQUIRED_MOCK_NAMES = (
    "project_analysis__stream_think",
    "project_analysis__stream_reasoning",
    "project_analysis__nonstream_think",
    "project_analysis__nonstream_reasoning",
)


@pytest.fixture
def mock_logger():
    return logging.getLogger("test_agent_workflow")


def load_mock_data(mock_name: str) -> dict:
    mock_dir = Path(__file__).parent / "mock_data" / mock_name
    with open(mock_dir / "mock_data.json", "r", encoding="utf-8") as f:
        return json.load(f)


def load_expected_output(mock_name: str) -> dict:
    mock_dir = Path(__file__).parent / "mock_data" / mock_name
    with open(mock_dir / "expected_output.json", "r", encoding="utf-8") as f:
        return json.load(f)


def _assert_mock_data_exists(mock_name: str) -> None:
    mock_dir = Path(__file__).parent / "mock_data" / mock_name
    mock_data_path = mock_dir / "mock_data.json"
    expected_path = mock_dir / "expected_output.json"
    assert mock_data_path.exists(), f"缺少基线 mock 数据: {mock_data_path}"
    assert expected_path.exists(), f"缺少基线 expected 输出: {expected_path}"


def _build_request(model: str, messages: list[dict], tools: list[dict], stream: bool) -> Request:
    request = AsyncMock(spec=Request)
    request.headers = Headers({"content-type": "application/json"})
    request.json = AsyncMock(
        return_value={
            "model": model,
            "messages": messages,
            "tools": tools,
            "stream": stream,
        }
    )
    return request


async def _collect_streaming_response(response) -> dict:
    chunks = []
    async for chunk in response.body_iterator:
        if isinstance(chunk, str):
            chunk = chunk.encode("utf-8")
        chunks.append(chunk)
    return parse_sse_chunks(collect_chunks_from_sse_body(b"".join(chunks)))


def _collect_json_response(response: JSONResponse) -> dict:
    return parse_nonstream_body(json.loads(response.body))


def _execute_tool(tool_name: str, arguments_str: str) -> str:
    try:
        arguments = json.loads(arguments_str)
    except json.JSONDecodeError:
        arguments = {}

    if tool_name == "list_files":
        return '["README.md", "src/", "package.json", "main.py", ".gitignore"]'
    if tool_name == "read_file":
        filename = arguments.get("filename", "")
        if filename == "README.md":
            return """# My Project

这是一个 Python Web 项目，主要功能包括：

## 功能特性

- RESTful API 路由
- 数据库 ORM 操作
- JWT 用户认证
- WebSocket 实时通信

## 技术栈

- Python 3.11+
- FastAPI
- SQLAlchemy
- Pydantic
"""
    return f"Unknown tool: {tool_name}"


def _append_tool_messages(messages: list[dict], parsed_response: dict) -> None:
    content = parsed_response["content"]
    for tool_call in parsed_response["tool_calls"]:
        messages.append(
            {
                "role": "assistant",
                "content": content,
                "tool_calls": [
                    {
                        "id": tool_call.get("id", ""),
                        "type": "function",
                        "function": {
                            "name": tool_call["function"]["name"],
                            "arguments": tool_call["function"]["arguments"],
                        },
                    }
                ],
            }
        )
        messages.append(
            {
                "role": "tool",
                "tool_call_id": tool_call.get("id", ""),
                "content": _execute_tool(tool_call["function"]["name"], tool_call["function"]["arguments"]),
            }
        )


def _assert_round_outputs(all_rounds_output: list[dict], backend_responses: list[dict], expected_final: dict) -> None:
    assert len(all_rounds_output) == len(backend_responses), (
        f"轮次数量不匹配: 期望 {len(backend_responses)}, 实际 {len(all_rounds_output)}"
    )

    for index, backend_resp in enumerate(backend_responses):
        round_output = all_rounds_output[index]
        expected_content = backend_resp.get("content", "") or ""
        expected_reasoning = backend_resp.get("reasoning_content", "") or ""

        if expected_content:
            assert normalize_text(round_output["content"]) == normalize_text(expected_content)
        if expected_reasoning:
            assert normalize_text(round_output["reasoning_content"]) == normalize_text(expected_reasoning)

    final_output = all_rounds_output[-1]
    if expected_final.get("content"):
        assert normalize_text(final_output["content"]) == normalize_text(expected_final["content"])
    if expected_final.get("reasoning_content"):
        assert normalize_text(final_output["reasoning_content"]) == normalize_text(expected_final["reasoning_content"])


async def _run_stream_loop(mock_name: str, model: str, matcher: ChunkConverterMatcher, backend_stream: bool) -> tuple[list[dict], dict]:
    _assert_mock_data_exists(mock_name)
    mock_data = load_mock_data(mock_name)
    expected = load_expected_output(mock_name)
    mock_server = create_mock_server(mock_data, stream=backend_stream)
    backends_config = BackendsConfig(
        groups=[],
        apis=[
            APIConfig(
                name="Mock Backend",
                endpoint="http://mock-backend",
                stream=backend_stream,
                custom_model_id=model,
                target_model_id="mock-model",
            )
        ],
    )
    handler = ProxyHandler(backends_config, logging.getLogger("test_agent_workflow"), matcher)

    messages = [{"role": "user", "content": f"分析项目 {mock_data['workflow']['input']['path']}"}]
    all_rounds_output = []

    async with httpx.AsyncClient(timeout=30.0, transport=mock_server.get_transport()) as http_client:
        await handler.set_client(http_client)

        for _ in range(10):
            response = await handler.handle_chat_completions(
                _build_request(model, messages, mock_data["workflow"]["tools"], stream=True)
            )
            assert response.status_code == 200
            parsed_response = await _collect_streaming_response(response)
            all_rounds_output.append(parsed_response)
            if not parsed_response["tool_calls"]:
                break
            _append_tool_messages(messages, parsed_response)

    _assert_round_outputs(all_rounds_output, mock_data["backend_responses"], expected["final_assembled_output"])
    return all_rounds_output, expected


async def _run_nonstream_loop(mock_name: str, model: str, matcher: ChunkConverterMatcher, request_stream: bool) -> tuple[list[dict], dict]:
    _assert_mock_data_exists(mock_name)
    mock_data = load_mock_data(mock_name)
    expected = load_expected_output(mock_name)
    mock_server = create_mock_server(mock_data, stream=False)
    backends_config = BackendsConfig(
        groups=[],
        apis=[
            APIConfig(
                name="Mock Backend",
                endpoint="http://mock-backend",
                stream=False,
                custom_model_id=model,
                target_model_id="mock-model",
            )
        ],
    )
    handler = ProxyHandler(backends_config, logging.getLogger("test_agent_workflow"), matcher)
    messages = [{"role": "user", "content": f"分析项目 {mock_data['workflow']['input']['path']}"}]
    all_rounds_output = []

    async with httpx.AsyncClient(timeout=30.0, transport=mock_server.get_transport()) as http_client:
        await handler.set_client(http_client)
        for _ in range(10):
            response = await handler.handle_chat_completions(
                _build_request(model, messages, mock_data["workflow"]["tools"], request_stream)
            )
            assert response.status_code == 200
            if request_stream:
                parsed_response = await _collect_streaming_response(response)
            else:
                assert isinstance(response, JSONResponse)
                parsed_response = _collect_json_response(response)
            all_rounds_output.append(parsed_response)
            if not parsed_response["tool_calls"]:
                break
            _append_tool_messages(messages, parsed_response)

    _assert_round_outputs(all_rounds_output, mock_data["backend_responses"], expected["final_assembled_output"])
    return all_rounds_output, expected["final_assembled_output"]


def test_mock_data_exists():
    for mock_name in REQUIRED_MOCK_NAMES:
        _assert_mock_data_exists(mock_name)


@pytest.mark.asyncio
@pytest.mark.replay
async def test_project_analysis__stream_think_full_loop(mock_logger):
    matcher = ChunkConverterMatcher({"think_tag": ["minimax"], "default": "reasoning_content"}, mock_logger)
    all_rounds_output, expected = await _run_stream_loop(
        mock_name="project_analysis__stream_think",
        model="minimax-project-analyzer",
        matcher=matcher,
        backend_stream=False,
    )
    assert normalize_text(all_rounds_output[-1]["content"]) == normalize_text(expected["final_assembled_output"]["content"])


@pytest.mark.asyncio
@pytest.mark.replay
async def test_project_analysis__stream_reasoning_full_loop(mock_logger):
    matcher = ChunkConverterMatcher({"reasoning": ["reasoning"], "default": "reasoning_content"}, mock_logger)
    all_rounds_output, expected = await _run_stream_loop(
        mock_name="project_analysis__stream_reasoning",
        model="reasoning-project-analyzer",
        matcher=matcher,
        backend_stream=False,
    )
    assert normalize_text(all_rounds_output[-1]["reasoning_content"]) == normalize_text(
        expected["final_assembled_output"]["reasoning_content"]
    )


@pytest.mark.asyncio
@pytest.mark.replay
async def test_project_analysis__nonstream_think_json(mock_logger):
    matcher = ChunkConverterMatcher({"think_tag": ["minimax"], "default": "reasoning_content"}, mock_logger)
    all_rounds_output, expected_final = await _run_nonstream_loop(
        mock_name="project_analysis__nonstream_think",
        model="minimax-nonstream",
        matcher=matcher,
        request_stream=False,
    )
    parsed_response = all_rounds_output[-1]
    assert not parsed_response["tool_calls"]
    assert normalize_text(parsed_response["content"]) == normalize_text(expected_final["content"])


@pytest.mark.asyncio
@pytest.mark.replay
async def test_project_analysis__nonstream_reasoning_json(mock_logger):
    matcher = ChunkConverterMatcher({"reasoning": ["reasoning"], "default": "reasoning_content"}, mock_logger)
    all_rounds_output, expected_final = await _run_nonstream_loop(
        mock_name="project_analysis__nonstream_reasoning",
        model="reasoning-nonstream",
        matcher=matcher,
        request_stream=False,
    )
    parsed_response = all_rounds_output[-1]
    assert not parsed_response["tool_calls"]
    assert normalize_text(parsed_response["reasoning_content"]) == normalize_text(expected_final["reasoning_content"])


@pytest.mark.asyncio
@pytest.mark.replay
async def test_project_analysis__nonstream_think_simulated_stream(mock_logger):
    matcher = ChunkConverterMatcher({"think_tag": ["minimax"], "default": "reasoning_content"}, mock_logger)
    all_rounds_output, expected_final = await _run_nonstream_loop(
        mock_name="project_analysis__nonstream_think",
        model="minimax-nonstream-stream",
        matcher=matcher,
        request_stream=True,
    )
    parsed_response = all_rounds_output[-1]
    assert parsed_response["done_seen"] is True
    assert normalize_text(parsed_response["content"]) == normalize_text(expected_final["content"])


@pytest.mark.asyncio
@pytest.mark.replay
async def test_project_analysis__nonstream_reasoning_simulated_stream(mock_logger):
    matcher = ChunkConverterMatcher({"reasoning": ["reasoning"], "default": "reasoning_content"}, mock_logger)
    all_rounds_output, expected_final = await _run_nonstream_loop(
        mock_name="project_analysis__nonstream_reasoning",
        model="reasoning-nonstream-stream",
        matcher=matcher,
        request_stream=True,
    )
    parsed_response = all_rounds_output[-1]
    assert parsed_response["done_seen"] is True
    assert normalize_text(parsed_response["reasoning_content"]) == normalize_text(expected_final["reasoning_content"])
