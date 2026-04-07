#!/usr/bin/env python3
"""
录制 Agent Workflow 脚本

直接调用后端 API，执行完整的 Agent Loop：
- 调用模型 → 模型返回 tool_calls → 执行 tool → 反馈结果 → 继续调用
- 直到模型返回最终回复（无 tool_calls）

保存：
- mock_data.json: 后端原始响应（每轮调用的 raw_chunks）
- expected_output.json: 最终组装输出
"""

import argparse
import asyncio
import json
import logging
import os
import sys
from datetime import datetime
from pathlib import Path
from typing import Optional

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

import httpx

from tests.helpers.request_signature import build_request_signature
from tests.helpers.response_parsing import parse_nonstream_body, parse_sse_chunks


logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
logger = logging.getLogger(__name__)


# ============== 测试用 Tools ==============

TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "list_files",
            "description": "列出指定路径下的所有文件和目录",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "要列出的目录路径"}
                },
                "required": ["path"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "read_file",
            "description": "读取指定文件的内容",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "文件所在的目录路径"},
                    "filename": {"type": "string", "description": "要读取的文件名"}
                },
                "required": ["path", "filename"]
            }
        }
    }
]


# ============== Tool 执行器 ==============

def execute_tool(tool_name: str, arguments: dict) -> str:
    """模拟执行 tool，返回结果"""
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
        else:
            return f"# {filename}\n\n文件内容..."

    return f'{{"error": "Unknown tool: {tool_name}"}}'


# ============== 录制逻辑 ==============


async def record_workflow(
    backend_endpoint: str,
    backend_api_key: str,
    backend_model: str,
    workflow_input: dict,
    stream: bool = True,
    max_turns: int = 20
) -> dict:
    """
    执行完整的 Agent Loop 并录制所有响应

    Returns:
        dict: {mock_data, expected_output}
    """
    mock_data = {
        "version": "1.0",
        "recorded_at": datetime.now().isoformat() + "Z",
        "workflow": {
            "input": workflow_input,
            "tools": TOOLS,
            "model": backend_model
        },
        "messages": [],
        "backend_responses": []
    }

    expected_output = {
        "version": "1.0",
        "workflow": {
            "input": workflow_input,
            "steps": []
        },
        "final_assembled_output": {
            "content": "",
            "reasoning_content": "",
            "tool_calls": []
        }
    }

    # 初始消息
    path = workflow_input["path"]
    initial_message = f"请分析项目 {path}，先列出项目文件，然后读取 README.md 文件，最后总结项目功能。"
    messages = [{"role": "user", "content": initial_message}]

    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {backend_api_key}"
    }

    turn = 0
    step = 0

    while turn < max_turns:
        turn += 1
        logger.info(f"=== Turn {turn} ===")

        # 调用后端 API
        async with httpx.AsyncClient(timeout=120.0) as client:
            request_body = {
                "model": backend_model,
                "messages": messages,
                "tools": TOOLS,
                "stream": stream
            }

            response = await client.post(
                f"{backend_endpoint}/chat/completions",
                json=request_body,
                headers=headers
            )

        if response.status_code != 200:
            logger.error(f"API 请求失败: {response.status_code} - {response.text}")
            break

        # 根据 stream 参数处理响应
        if stream:
            # 流式响应：收集 SSE chunks
            raw_chunks = []
            async for line in response.aiter_lines():
                if line:
                    raw_chunks.append(line)
            parsed = parse_sse_chunks(raw_chunks)
        else:
            # 非流式响应：直接解析 JSON
            response_json = response.json()
            parsed = parse_nonstream_body(response_json)
            raw_chunks = [json.dumps(response_json)]

        logger.info(f"Content: {parsed['content'][:100] if parsed['content'] else 'None'}...")
        logger.info(f"Reasoning: {parsed['reasoning_content'][:50] if parsed['reasoning_content'] else 'None'}...")
        logger.info(f"Tool calls: {len(parsed['tool_calls'])}")

        # 保存后端响应
        mock_data["backend_responses"].append({
            "step": turn,
            "request_signature": build_request_signature(request_body),
            "raw_chunks": raw_chunks,
            "reasoning_content": parsed["reasoning_content"] or None,
            "content": parsed["content"]
        })

        # 如果有 tool_calls，执行并继续
        if parsed["tool_calls"]:
            for tc in parsed["tool_calls"]:
                tool_name = tc["function"]["name"]
                tool_args = tc["function"]["arguments"]
                tool_id = tc["id"]

                step += 1
                logger.info(f"执行 tool: {tool_name}, 参数: {tool_args}")

                # 解析参数
                try:
                    args_dict = json.loads(tool_args)
                except json.JSONDecodeError:
                    args_dict = {}

                # 执行 tool
                tool_result = execute_tool(tool_name, args_dict)

                # 保存 step 结果
                expected_output["workflow"]["steps"].append({
                    "step": step,
                    "tool": tool_name,
                    "arguments": args_dict,
                    "result": tool_result
                })
                expected_output["final_assembled_output"]["tool_calls"].append({
                    "name": tool_name,
                    "arguments": tool_args
                })

                # 添加 assistant 消息和 tool 结果消息
                messages.append({
                    "role": "assistant",
                    "content": parsed["content"],
                    "tool_calls": [tc]
                })
                messages.append({
                    "role": "tool",
                    "tool_call_id": tool_id,
                    "content": tool_result
                })

            # 继续下一轮
            continue

        # 没有 tool_calls，这是最终回复
        expected_output["final_assembled_output"]["content"] = parsed["content"]
        expected_output["final_assembled_output"]["reasoning_content"] = parsed["reasoning_content"]
        mock_data["messages"] = messages

        logger.info(f"最终回复: {parsed['content'][:100]}...")
        break

    return {"mock_data": mock_data, "expected_output": expected_output}


async def main():
    parser = argparse.ArgumentParser(
        description="录制 Agent Workflow - 完整的 Agent Loop",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
示例:
    # 使用环境变量
    export MINIMAX_API_KEY=your_key_here
    python scripts/record_agent_workflow.py \\
        --output tests/fixtures/mock_data/project_analysis__stream_think \\
        --backend-endpoint https://api.minimaxi.com/v1 \\
        --backend-model MiniMax-M2.7

    # 或直接指定 key
    python scripts/record_agent_workflow.py \\
        --output tests/fixtures/mock_data/project_analysis__stream_think \\
        --backend-endpoint https://api.minimaxi.com/v1 \\
        --backend-api-key your_key_here \\
        --backend-model MiniMax-M2.7

    # 非流式录制
    python scripts/record_agent_workflow.py \\
        --output tests/fixtures/mock_data/project_analysis__nonstream_think \\
        --backend-endpoint https://api.minimaxi.com/v1 \\
        --backend-api-key your_key_here \\
        --backend-model MiniMax-M2.7 \\
        --stream false
"""
    )
    parser.add_argument("--output", required=True, help="输出目录")
    parser.add_argument("--backend-endpoint", required=True, help="后端 API Endpoint")
    parser.add_argument("--backend-api-key", default=os.environ.get("MINIMAX_API_KEY", ""), help="后端 API Key (可从环境变量 MINIMAX_API_KEY 读取)")
    parser.add_argument("--backend-model", required=True, help="后端模型 ID")
    parser.add_argument("--path", default="/path/to/my-project", help="要分析的项目路径")
    parser.add_argument("--max-turns", type=int, default=20, help="最大循环次数")
    parser.add_argument("--stream", type=bool, default=True, help="是否使用流式请求 (true/false)")

    args = parser.parse_args()

    # 检查 API Key
    if not args.backend_api_key:
        logger.error("API Key 未设置，请通过 --backend-api-key 参数或 MINIMAX_API_KEY 环境变量提供")
        sys.exit(1)

    output_dir = Path(args.output)
    output_dir.mkdir(parents=True, exist_ok=True)

    workflow_input = {"path": args.path}

    logger.info("=" * 60)
    logger.info("开始录制 Agent Workflow")
    logger.info(f"Backend: {args.backend_endpoint}")
    logger.info(f"Model: {args.backend_model}")
    logger.info("=" * 60)

    result = await record_workflow(
        backend_endpoint=args.backend_endpoint,
        backend_api_key=args.backend_api_key,
        backend_model=args.backend_model,
        workflow_input=workflow_input,
        stream=args.stream,
        max_turns=args.max_turns
    )

    mock_data = result["mock_data"]
    expected_output = result["expected_output"]

    # 保存
    with open(output_dir / "mock_data.json", "w", encoding="utf-8") as f:
        json.dump(mock_data, f, ensure_ascii=False, indent=2)

    with open(output_dir / "expected_output.json", "w", encoding="utf-8") as f:
        json.dump(expected_output, f, ensure_ascii=False, indent=2)

    logger.info("=" * 60)
    logger.info("录制完成！")
    logger.info(f"Tool 调用次数: {len(expected_output['workflow']['steps'])}")
    logger.info(f"输出目录: {args.output}")
    logger.info("=" * 60)


if __name__ == "__main__":
    asyncio.run(main())
