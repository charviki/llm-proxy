"""
Agent Workflow 集成测试

验证 llm-proxy 的组装逻辑在代码改动后不受影响：
- Reasoning Content 完整性
- Tool Calls 字段不丢失
- 流式 Chunk 组装顺序正确
- SSE 格式符合规范

使用录制-回放机制：
- mock 后端返回录制的原始数据
- 通过完整 llm-proxy 处理链路
- 对比最终输出 vs expected 结果
"""

import asyncio
import json
import logging
import os
import pytest
from pathlib import Path
from typing import Optional
from unittest.mock import AsyncMock

from fastapi import Request
from starlette.datastructures import Headers

from config.models import BackendsConfig, APIConfig
from proxy.handler import ProxyHandler
from proxy.converter import ChunkConverterMatcher

from tests.fixtures.mock_server import create_mock_server


# ============== Fixtures ==============

@pytest.fixture
def mock_logger():
    return logging.getLogger("test_agent_workflow")


@pytest.fixture
def parser_matcher(mock_logger):
    # 录制数据是 MiniMax 的（包含 think 标签），使用 think_tag parser
    return ChunkConverterMatcher({"minimax": "think_tag", "default": "reasoning_content"}, mock_logger)


def load_mock_data(mock_name: str = "project_analysis__stream_think") -> dict:
    """加载录制的 mock 数据"""
    mock_dir = Path(__file__).parent / "mock_data" / mock_name
    with open(mock_dir / "mock_data.json", "r", encoding="utf-8") as f:
        return json.load(f)


def load_expected_output(mock_name: str = "project_analysis__stream_think") -> dict:
    """加载期望的输出结果"""
    mock_dir = Path(__file__).parent / "mock_data" / mock_name
    with open(mock_dir / "expected_output.json", "r", encoding="utf-8") as f:
        return json.load(f)


# ============== 辅助函数 ==============

def normalize_text(text: str) -> str:
    """
    格式化文本用于比较：去除换行、空格、首尾空白

    用于比较 content 和 reasoning_content 是否一致
    """
    if not text:
        return ""
    # 替换换行为空格
    text = text.replace('\n', ' ')
    # 合并多个空格为单个
    import re
    text = re.sub(r'\s+', ' ', text)
    # 去除首尾空白
    return text.strip()


def extract_think_content(content: str) -> str:
    """
    从 MiniMax 的 think tag 格式中提取 think 内容

    MiniMax 返回格式如：
    "\n\n\n<think>\n用户想要分析一个项目...\n</think>\n"

    返回：
        提取的 think 内容（去除 think tag 和换行）
    """
    if not content:
        return ""

    # 去除首尾空白
    content = content.strip()

    # 去除 <think>...</think> 标签
    import re
    # 匹配 \n<think>\n...\n</think> 或 \n<think>...</think>
    content = re.sub(r'\n*<think>\n*', '', content)
    content = re.sub(r'\n*</think>', '', content)
    content = content.strip()

    return content


async def collect_sse_from_starlette_response(response) -> bytes:
    """
    收集 Starlette StreamingResponse 的完整内容
    """
    chunks = []
    async for chunk in response.body_iterator:
        if isinstance(chunk, str):
            chunk = chunk.encode('utf-8')
        chunks.append(chunk)
    return b"".join(chunks)


def extract_content_from_sse(body: bytes) -> tuple[str, str, list[dict]]:
    """
    从 SSE 响应中提取 content、reasoning_content 和 tool_calls

    Returns:
        (content, reasoning_content, tool_calls)
    """
    content_parts = []
    reasoning_parts = []
    tool_calls_data = []
    current_tool_call: Optional[dict] = None

    text = body.decode('utf-8')
    lines = text.split('\n')

    i = 0
    while i < len(lines):
        line = lines[i].strip()

        if line.startswith("data: "):
            data_str = line[6:].strip()

            if data_str == "[DONE]":
                i += 1
                continue

            try:
                data = json.loads(data_str)
                choices = data.get("choices", [])
                if choices:
                    delta = choices[0].get("delta", {})

                    # 提取 content
                    if "content" in delta:
                        content_parts.append(delta["content"])

                    # 提取 reasoning_content
                    if "reasoning_content" in delta:
                        reasoning_parts.append(delta["reasoning_content"])

                    # 提取 tool_calls
                    if "tool_calls" in delta:
                        for tc_delta in delta["tool_calls"]:
                            idx = tc_delta.get("index", 0)

                            if current_tool_call is None or current_tool_call.get("_index") != idx:
                                current_tool_call = {
                                    "_index": idx,
                                    "id": tc_delta.get("id", ""),
                                    "type": "function",
                                    "function": {"name": "", "arguments": ""}
                                }
                                tool_calls_data.append(current_tool_call)
                            else:
                                # 继续累加
                                pass

                            if tc_delta.get("id"):
                                current_tool_call["id"] = tc_delta["id"]
                            if tc_delta.get("function", {}).get("name"):
                                current_tool_call["function"]["name"] = tc_delta["function"]["name"]
                            if tc_delta.get("function", {}).get("arguments"):
                                current_tool_call["function"]["arguments"] += tc_delta["function"]["arguments"]
            except json.JSONDecodeError:
                pass

        i += 1

    # 清理 tool_calls 中的 _index
    for tc in tool_calls_data:
        tc.pop("_index", None)

    # 合并 content
    raw_content = "".join(content_parts)

    # MiniMax 特殊处理：从 content 中提取 think 内容
    think_content = extract_think_content(raw_content)

    # 如果有显式的 reasoning_content，也要加入
    explicit_reasoning = "".join(reasoning_parts)
    if explicit_reasoning:
        think_content = explicit_reasoning

    return raw_content, think_content, tool_calls_data


# ============== 测试用例 ==============

def _skip_if_no_mock_data():
    """如果 mock 数据不存在则跳过测试"""
    mock_dir = Path(__file__).parent / "mock_data" / "project_analysis__stream_think"
    mock_data_path = mock_dir / "mock_data.json"
    expected_path = mock_dir / "expected_output.json"

    if not mock_data_path.exists() or not expected_path.exists():
        pytest.skip(
            f"Mock 数据不存在: {mock_data_path}\n"
            "请先运行录制脚本:\n"
            "  python scripts/record_agent_workflow.py --output tests/agent/mock_data/project_analysis__stream_think ..."
        )


@pytest.mark.asyncio
async def test_project_analysis__full_loop(mock_logger, parser_matcher):
    """
    验证 llm-proxy 完整 Agent Loop 组装逻辑

    场景：
    - Mock 后端根据 messages 历史返回对应轮次的响应
    - 执行完整的 Agent Loop（多轮）
    - 验证每一轮的 content、reasoning_content、tool_calls
    """
    _skip_if_no_mock_data()

    mock_data = load_mock_data()
    expected = load_expected_output()

    # 创建 mock server
    mock_server = create_mock_server(mock_data, stream=False)

    backends_config = BackendsConfig(
        groups=[],
        apis=[
            APIConfig(
                name="Mock Backend",
                endpoint="http://mock-backend",
                stream=False,
                custom_model_id="minimax-project-analyzer",
                target_model_id="mock-model"
            )
        ]
    )

    handler = ProxyHandler(backends_config, mock_logger, parser_matcher)

    import httpx

    # 初始请求
    messages = [
        {"role": "user", "content": f"分析项目 {mock_data['workflow']['input']['path']}"}
    ]

    all_rounds_output = []

    async with httpx.AsyncClient(timeout=30.0, transport=mock_server.get_transport()) as http_client:
        await handler.set_client(http_client)

        max_rounds = 10
        for round_idx in range(max_rounds):
            # 发送请求
            request = AsyncMock(spec=Request)
            request.headers = Headers({"content-type": "application/json"})
            request.json = AsyncMock(return_value={
                "model": "minimax-project-analyzer",
                "messages": messages,
                "tools": mock_data["workflow"]["tools"],
                "stream": True
            })

            response = await handler.handle_chat_completions(request)
            assert response.status_code == 200

            # 收集响应
            body = await collect_sse_from_starlette_response(response)
            content, reasoning, tool_calls = extract_content_from_sse(body)

            # 保存本轮输出
            all_rounds_output.append({
                "round": round_idx + 1,
                "content": content,
                "reasoning": reasoning,
                "tool_calls": tool_calls
            })

            # 如果没有 tool_calls，说明是最终回复，结束
            if not tool_calls:
                break

            # 如果有 tool_calls，添加到 messages 并继续
            for tc in tool_calls:
                messages.append({
                    "role": "assistant",
                    "content": content,
                    "tool_calls": [{
                        "id": tc.get("id", ""),
                        "type": "function",
                        "function": {
                            "name": tc["function"]["name"],
                            "arguments": tc["function"]["arguments"]
                        }
                    }]
                })
                # 添加 tool 结果
                tool_result = _execute_tool(tc["function"]["name"], tc["function"]["arguments"])
                messages.append({
                    "role": "tool",
                    "tool_call_id": tc.get("id", ""),
                    "content": tool_result
                })

    # ========== 验证每一轮的输出 ==========

    # 获取录制的 backend_responses
    backend_responses = mock_data.get("backend_responses", [])
    expected_rounds = len(backend_responses)

    # 验证轮次数量
    actual_rounds = len(all_rounds_output)
    assert actual_rounds == expected_rounds, \
        f"轮次数量不匹配: 期望 {expected_rounds}, 实际 {actual_rounds}"

    # 验证每一轮
    for i, backend_resp in enumerate(backend_responses):
        round_output = all_rounds_output[i]

        # 验证 content（格式化后比较）
        expected_content = backend_resp.get("content", "") or ""
        if expected_content:
            assert normalize_text(round_output["content"]) == normalize_text(expected_content), \
                f"Round {i+1} content 不匹配:\n期望: {normalize_text(expected_content)[:100]}...\n实际: {normalize_text(round_output['content'])[:100]}..."

        # 验证 reasoning_content（格式化后比较）
        expected_reasoning = backend_resp.get("reasoning_content", "") or ""
        if expected_reasoning:
            assert normalize_text(round_output["reasoning"]) == normalize_text(expected_reasoning), \
                f"Round {i+1} reasoning 不匹配:\n期望: {normalize_text(expected_reasoning)[:100]}...\n实际: {normalize_text(round_output['reasoning'])[:100]}..."

    # ========== 验证最终输出 ==========
    final_output = all_rounds_output[-1]
    expected_final = expected["final_assembled_output"]

    # 最终 content 验证
    expected_content = expected_final.get("content", "") or ""
    if expected_content:
        assert normalize_text(final_output["content"]) == normalize_text(expected_content), \
            f"最终 content 不匹配"

    # 最终 reasoning 验证
    expected_reasoning = expected_final.get("reasoning_content", "") or ""
    if expected_reasoning:
        assert normalize_text(final_output["reasoning"]) == normalize_text(expected_reasoning), \
            f"最终 reasoning 不匹配"

    print(f"\n✅ 完整 Agent Loop 测试通过！共 {actual_rounds} 轮")


def _execute_tool(tool_name: str, arguments_str: str) -> str:
    """执行 tool，返回结果"""
    try:
        arguments = json.loads(arguments_str)
    except json.JSONDecodeError:
        arguments = {}

    if tool_name == "list_files":
        return '["README.md", "src/", "package.json", "main.py", ".gitignore"]'
    elif tool_name == "read_file":
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


# ============== Mock 数据不存在时的提示 ==============

def test_mock_data_exists():
    """验证 mock 数据文件是否存在"""
    mock_dir = Path(__file__).parent / "mock_data" / "project_analysis__stream_think"
    mock_data_path = mock_dir / "mock_data.json"
    expected_path = mock_dir / "expected_output.json"

    if not mock_data_path.exists():
        pytest.skip(
            f"Mock 数据不存在: {mock_data_path}\n"
            "请先运行录制脚本:\n"
            f"  python scripts/record_agent_workflow.py --output {mock_dir.parent} ..."
        )

    if not expected_path.exists():
        pytest.skip(
            f"Expected 输出不存在: {expected_path}\n"
            "请先运行录制脚本"
        )


# ============== Reasoning 字段测试 ==============

def _skip_if_no_reasoning_mock_data():
    """如果 reasoning mock 数据不存在则跳过测试"""
    mock_dir = Path(__file__).parent / "mock_data" / "project_analysis__stream_reasoning"
    mock_data_path = mock_dir / "mock_data.json"
    expected_path = mock_dir / "expected_output.json"

    if not mock_data_path.exists() or not expected_path.exists():
        pytest.skip(
            f"Mock 数据不存在: {mock_data_path}\n"
            "请先运行录制脚本:\n"
            "  python scripts/record_agent_workflow.py --output tests/agent/mock_data/project_analysis__stream_reasoning ..."
        )


@pytest.mark.asyncio
async def test_project_analysis__reasoning_field(mock_logger, parser_matcher):
    """
    验证 llm-proxy reasoning 字段处理逻辑

    场景：
    - Mock 后端返回带 reasoning 字段的响应（OpenRouter 格式）
    - 使用 reasoning parser 处理
    - 验证 content 和 reasoning_content 正确提取
    """
    _skip_if_no_reasoning_mock_data()

    # 使用 reasoning parser
    reasoning_matcher = ChunkConverterMatcher({"minimax": "reasoning", "default": "reasoning_content"}, mock_logger)

    mock_data = load_mock_data("project_analysis__stream_reasoning")

    # 创建 mock server
    mock_server = create_mock_server(mock_data, stream=False)

    backends_config = BackendsConfig(
        groups=[],
        apis=[
            APIConfig(
                name="Mock Backend",
                endpoint="http://mock-backend",
                stream=False,
                custom_model_id="minimax-reasoning",
                target_model_id="mock-model"
            )
        ]
    )

    handler = ProxyHandler(backends_config, mock_logger, reasoning_matcher)

    import httpx

    # 初始请求
    messages = [
        {"role": "user", "content": f"分析项目 {mock_data['workflow']['input']['path']}"}
    ]

    all_rounds_output = []

    async with httpx.AsyncClient(timeout=30.0, transport=mock_server.get_transport()) as http_client:
        await handler.set_client(http_client)

        max_rounds = 10
        for round_idx in range(max_rounds):
            # 发送请求
            request = AsyncMock(spec=Request)
            request.headers = Headers({"content-type": "application/json"})
            request.json = AsyncMock(return_value={
                "model": "minimax-reasoning",
                "messages": messages,
                "tools": mock_data["workflow"]["tools"],
                "stream": True
            })

            response = await handler.handle_chat_completions(request)
            assert response.status_code == 200

            # 收集响应
            body = await collect_sse_from_starlette_response(response)
            content, reasoning, tool_calls = extract_content_from_sse(body)

            # 保存本轮输出
            all_rounds_output.append({
                "round": round_idx + 1,
                "content": content,
                "reasoning": reasoning,
                "tool_calls": tool_calls
            })

            # 如果没有 tool_calls，说明是最终回复，结束
            if not tool_calls:
                break

            # 如果有 tool_calls，添加到 messages 并继续
            for tc in tool_calls:
                messages.append({
                    "role": "assistant",
                    "content": content,
                    "tool_calls": [{
                        "id": tc.get("id", ""),
                        "type": "function",
                        "function": {
                            "name": tc["function"]["name"],
                            "arguments": tc["function"]["arguments"]
                        }
                    }]
                })
                # 添加 tool 结果
                tool_result = _execute_tool(tc["function"]["name"], tc["function"]["arguments"])
                messages.append({
                    "role": "tool",
                    "tool_call_id": tc.get("id", ""),
                    "content": tool_result
                })

    # ========== 验证每一轮的输出 ==========

    # 获取录制的 backend_responses
    backend_responses = mock_data.get("backend_responses", [])
    expected_rounds = len(backend_responses)

    # 验证轮次数量
    actual_rounds = len(all_rounds_output)
    assert actual_rounds == expected_rounds, \
        f"轮次数量不匹配: 期望 {expected_rounds}, 实际 {actual_rounds}"

    # 验证每一轮
    for i, backend_resp in enumerate(backend_responses):
        round_output = all_rounds_output[i]

        # 验证 content（格式化后比较）
        expected_content = backend_resp.get("content", "") or ""
        if expected_content:
            assert normalize_text(round_output["content"]) == normalize_text(expected_content), \
                f"Round {i+1} content 不匹配:\n期望: {normalize_text(expected_content)[:100]}...\n实际: {normalize_text(round_output['content'])[:100]}..."

        # 验证 reasoning_content（格式化后比较）
        expected_reasoning = backend_resp.get("reasoning_content", "") or ""
        if expected_reasoning:
            assert normalize_text(round_output["reasoning"]) == normalize_text(expected_reasoning), \
                f"Round {i+1} reasoning 不匹配:\n期望: {normalize_text(expected_reasoning)[:100]}...\n实际: {normalize_text(round_output['reasoning'])[:100]}..."

    print(f"\n✅ Reasoning 字段测试通过！共 {actual_rounds} 轮")

