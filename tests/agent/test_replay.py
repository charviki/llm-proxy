#!/usr/bin/env python3
"""录制重放功能测试"""
import pytest
import asyncio
import json
import sys
import shutil
from pathlib import Path

# 添加项目根目录到 sys.path
PROJECT_ROOT = Path(__file__).parent.parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from proxy.context import set_replay_id, clear_replay_id, get_replay_id
from proxy.transport import ReplayMiddleware, ProxyTransport
import httpx
import logging

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("test_replay")


@pytest.fixture
def temp_recordings_dir(tmp_path):
    """提供一个临时的录制目录"""
    recordings_dir = tmp_path / "recordings"
    recordings_dir.mkdir()
    return recordings_dir


@pytest.mark.asyncio
async def test_replay_middleware(temp_recordings_dir):
    """测试 ReplayMiddleware 基本功能"""
    middleware = ReplayMiddleware(recordings_dir=temp_recordings_dir, logger=logger)

    # 测试 _path_to_prefix
    assert ReplayMiddleware._path_to_prefix("/v1/chat/completions") == "v1_chat_completions"
    assert ReplayMiddleware._path_to_prefix("/v1/completions") == "v1_completions"


@pytest.mark.asyncio
async def test_context_vars():
    """测试 context vars 基本功能"""
    # 测试 replay_id 设置和获取
    set_replay_id("test_123")
    assert get_replay_id() == "test_123"
    clear_replay_id()
    assert get_replay_id() is None


@pytest.mark.asyncio
async def test_replay_file_exists(temp_recordings_dir):
    """测试重放文件查找"""
    replay_id = "test_replay_id"
    prefix = "v1_chat_completions"
    
    # 准备测试文件
    mock_data = {
        "status_code": 200,
        "chunks": ["data: {\"test\": 1}\n\n", "data: [DONE]\n\n"]
    }
    
    correct_path = temp_recordings_dir / f"{prefix}__{replay_id}__backend_response.json"
    with open(correct_path, "w") as f:
        json.dump(mock_data, f)

    assert correct_path.exists()
    
    with open(correct_path, 'r') as f:
        data = json.load(f)

    assert data.get('status_code') == 200
    assert len(data.get('chunks', [])) == 2


@pytest.mark.asyncio
async def test_replay_middleware_e2e(temp_recordings_dir):
    """端到端测试：使用 ReplayMiddleware 模拟请求"""
    
    # 准备测试文件
    replay_id = "test_replay_id"
    prefix = "v1_chat_completions"
    mock_data = {
        "status_code": 200,
        "body": {"ok": True}
    }
    correct_path = temp_recordings_dir / f"{prefix}__{replay_id}__backend_response.json"
    with open(correct_path, "w") as f:
        json.dump(mock_data, f)

    # 创建中间件
    middleware = ReplayMiddleware(recordings_dir=temp_recordings_dir, logger=logger)

    # 创建请求
    request = httpx.Request("POST", "http://test.com/v1/chat/completions", json={})

    # 设置 replay_id
    set_replay_id(replay_id)

    # 模拟 next_handler（不应该被调用）
    async def next_handler():
        pytest.fail("next_handler 被调用了，不应该出现这种情况！")
        return httpx.Response(status_code=500, json={"error": "should not be called"})

    try:
        # 调用中间件
        response = await middleware(request, next_handler)
        assert response.status_code == 200
        
        # 验证读取到的数据
        data = await response.aread()
        assert json.loads(data) == {"ok": True}
    finally:
        # 清除 replay_id
        clear_replay_id()


@pytest.mark.asyncio
async def test_no_replay_fallback(temp_recordings_dir):
    """测试没有 replay_id 时正确 fallback"""
    middleware = ReplayMiddleware(recordings_dir=temp_recordings_dir, logger=logger)

    request = httpx.Request("POST", "http://test.com/v1/chat/completions", json={})

    called = False
    async def next_handler():
        nonlocal called
        called = True
        return httpx.Response(status_code=200, json={"ok": True})

    response = await middleware(request, next_handler)

    assert called is True
    assert response.status_code == 200


async def main():
    print("=" * 60)
    print("录制重放功能测试")
    print("=" * 60)
    print()

    await test_context_vars()
    print()

    await test_replay_middleware()
    print()

    await test_replay_file_exists()
    print()

    await test_replay_middleware_e2e()
    print()

    await test_no_replay_fallback()
    print()

    print("=" * 60)
    print("测试完成")
    print("=" * 60)


if __name__ == "__main__":
    asyncio.run(main())
