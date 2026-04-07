#!/usr/bin/env python3
"""录制重放功能测试"""
import json
import logging
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest
from fastapi import FastAPI

from config.models import APIConfig, BackendsConfig
from proxy.context import clear_replay_id, get_replay_id, set_replay_id
from proxy.converter import ChunkConverterMatcher
from proxy.handler import ProxyHandler
from proxy.middleware import RecordingMiddleware
from proxy.recording_interceptor import TransportRecordingMiddleware
from proxy.transport import ProxyTransport, ReplayMiddleware
from routes import register_routes
from tests.helpers.response_parsing import parse_sse_chunks

logger = logging.getLogger("test_replay")


@pytest.fixture
def temp_recordings_dir(tmp_path):
    recordings_dir = tmp_path / "recordings"
    recordings_dir.mkdir()
    return recordings_dir


def _write_backend_replay_file(recordings_dir: Path, replay_id: str, payload: dict) -> Path:
    replay_path = recordings_dir / f"v1_chat_completions__{replay_id}__backend_response.json"
    with open(replay_path, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False)
    return replay_path


def _build_recording_config():
    return SimpleNamespace(recording=SimpleNamespace(record_paths=["/v1/chat/completions"]))


async def _collect_streaming_response(response: httpx.Response) -> dict:
    chunks = []
    async for line in response.aiter_lines():
        if line:
            chunks.append(line)
    return parse_sse_chunks(chunks)


@pytest.mark.asyncio
@pytest.mark.replay
async def test_replay_middleware_prefix_and_context(temp_recordings_dir):
    middleware = ReplayMiddleware(recordings_dir=temp_recordings_dir, logger=logger)
    assert ReplayMiddleware._path_to_prefix("/v1/chat/completions") == "v1_chat_completions"
    assert ReplayMiddleware._path_to_prefix("/v1/completions") == "v1_completions"

    set_replay_id("test_123")
    assert get_replay_id() == "test_123"
    clear_replay_id()
    assert get_replay_id() is None

    request = httpx.Request("POST", "http://test.com/v1/chat/completions", json={})
    called = False

    async def next_handler():
        nonlocal called
        called = True
        return httpx.Response(status_code=200, json={"ok": True})

    response = await middleware(request, next_handler)
    assert called is True
    assert response.status_code == 200


@pytest.mark.asyncio
@pytest.mark.replay
async def test_replay_middleware_reads_recorded_file(temp_recordings_dir):
    replay_id = "test_replay_id"
    replay_path = _write_backend_replay_file(
        temp_recordings_dir,
        replay_id,
        {"status_code": 200, "body": {"ok": True}},
    )
    assert replay_path.exists()

    middleware = ReplayMiddleware(recordings_dir=temp_recordings_dir, logger=logger)
    request = httpx.Request("POST", "http://test.com/v1/chat/completions", json={})

    set_replay_id(replay_id)
    try:
        async def next_handler():
            pytest.fail("命中 replay 文件时不应继续调用下游 handler")

        response = await middleware(request, next_handler)
        assert response.status_code == 200
        assert json.loads(await response.aread()) == {"ok": True}
    finally:
        clear_replay_id()


@pytest.mark.asyncio
@pytest.mark.replay
async def test_replay_full_app_e2e_uses_recorded_backend_response(temp_recordings_dir):
    replay_id = "replay_e2e_case"
    _write_backend_replay_file(
        temp_recordings_dir,
        replay_id,
        {
            "status_code": 200,
            "body": {
                "id": "chatcmpl-replay",
                "object": "chat.completion",
                "created": 1700000000,
                "model": "mock-model",
                "choices": [
                    {
                        "index": 0,
                        "message": {
                            "role": "assistant",
                            "content": "<think>分析中</think>最终答案",
                        },
                        "finish_reason": "stop",
                    }
                ],
            },
        },
    )

    middlewares = [
        ReplayMiddleware(recordings_dir=temp_recordings_dir, logger=logger),
        TransportRecordingMiddleware(logger=logger),
    ]
    transport = ProxyTransport(logger=logger, middlewares=middlewares)
    upstream_client = httpx.AsyncClient(timeout=30.0, transport=transport)

    handler = ProxyHandler(
        backends=BackendsConfig(
            groups=[],
            apis=[
                APIConfig(
                    name="Replay Backend",
                    endpoint="https://unused-backend.example",
                    stream=False,
                    custom_model_id="minimax-replay-model",
                    target_model_id="mock-model",
                )
            ],
        ),
        logger=logger,
        parser_matcher=ChunkConverterMatcher({"think_tag": ["minimax"], "default": "reasoning_content"}, logger),
    )
    await handler.set_client(upstream_client)

    app = FastAPI()
    app.add_middleware(RecordingMiddleware, config=_build_recording_config(), logger=logger)
    register_routes(app, handler)

    before_files = sorted(path.name for path in temp_recordings_dir.glob("*.json"))
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://testserver",
    ) as client:
        response = await client.post(
            "/v1/chat/completions",
            headers={"X-Replay-Id": replay_id, "Content-Type": "application/json"},
            json={
                "model": "minimax-replay-model",
                "messages": [{"role": "user", "content": "请给出答案"}],
                "stream": True,
            },
        )
        assert response.status_code == 200
        parsed_response = await _collect_streaming_response(response)

    after_files = sorted(path.name for path in temp_recordings_dir.glob("*.json"))
    await upstream_client.aclose()

    assert before_files == after_files
    assert parsed_response["content"] == "最终答案"
    assert parsed_response["reasoning_content"] == "分析中"
    assert parsed_response["done_seen"] is True
    assert get_replay_id() is None
