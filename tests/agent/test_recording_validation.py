import json

import pytest

from tests.agent.validate_recordings import validate_recording


def _write_recording_file(recordings_dir, prefix: str, suffix: str, file_type: str, payload: dict) -> None:
    path = recordings_dir / f"{prefix}__{suffix}__{file_type}.json"
    with open(path, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False)


@pytest.mark.recording_validation
def test_validate_recording_streaming_case(tmp_path):
    recordings_dir = tmp_path / "recordings"
    recordings_dir.mkdir()

    prefix = "v1_chat_completions"
    suffix = "stream_case"
    request_body = {"model": "demo-model", "messages": [{"role": "user", "content": "hello"}]}
    backend_body = {"model": "real-model", "messages": request_body["messages"]}

    _write_recording_file(
        recordings_dir,
        prefix,
        suffix,
        "client_request",
        {
            "prefix": prefix,
            "suffix": suffix,
            "method": "POST",
            "endpoint": "/v1/chat/completions",
            "headers": {"content-type": "application/json"},
            "body": request_body,
        },
    )
    _write_recording_file(
        recordings_dir,
        prefix,
        suffix,
        "backend_request",
        {
            "prefix": prefix,
            "suffix": suffix,
            "method": "POST",
            "endpoint": "/v1/chat/completions",
            "headers": {"content-type": "application/json"},
            "body": backend_body,
        },
    )
    _write_recording_file(
        recordings_dir,
        prefix,
        suffix,
        "backend_response",
        {
            "status_code": 200,
            "headers": {"content-type": "text/event-stream"},
            "chunks": [
                'data: {"choices":[{"delta":{"content":"Hello"},"finish_reason":null}]}',
                'data: {"choices":[{"delta":{"content":" world"},"finish_reason":"stop"}]}',
                "data: [DONE]",
            ],
        },
    )
    _write_recording_file(
        recordings_dir,
        prefix,
        suffix,
        "client_response",
        {
            "status_code": 200,
            "headers": {"content-type": "text/event-stream"},
            "chunks": [
                'data: {"choices":[{"delta":{"content":"Hello"},"finish_reason":null}]}',
                'data: {"choices":[{"delta":{"content":" world"},"finish_reason":"stop"}]}',
                "data: [DONE]",
            ],
        },
    )

    result = validate_recording(prefix, suffix, recordings_dir)
    assert result.passed is True
    assert result.errors == []


@pytest.mark.recording_validation
def test_validate_recording_nonstream_case(tmp_path):
    recordings_dir = tmp_path / "recordings"
    recordings_dir.mkdir()

    prefix = "v1_chat_completions"
    suffix = "nonstream_case"
    request_body = {"model": "demo-model", "messages": [{"role": "user", "content": "hello"}]}
    backend_body = {"model": "real-model", "messages": request_body["messages"]}
    response_body = {
        "id": "chatcmpl-1",
        "choices": [
            {
                "message": {
                    "role": "assistant",
                    "content": "<think>plan</think>answer",
                },
                "finish_reason": "stop",
            }
        ],
    }

    _write_recording_file(
        recordings_dir,
        prefix,
        suffix,
        "client_request",
        {
            "prefix": prefix,
            "suffix": suffix,
            "method": "POST",
            "endpoint": "/v1/chat/completions",
            "headers": {"content-type": "application/json"},
            "body": request_body,
        },
    )
    _write_recording_file(
        recordings_dir,
        prefix,
        suffix,
        "backend_request",
        {
            "prefix": prefix,
            "suffix": suffix,
            "method": "POST",
            "endpoint": "/v1/chat/completions",
            "headers": {"content-type": "application/json"},
            "body": backend_body,
        },
    )
    _write_recording_file(
        recordings_dir,
        prefix,
        suffix,
        "backend_response",
        {
            "status_code": 200,
            "headers": {"content-type": "application/json"},
            "body": response_body,
        },
    )
    _write_recording_file(
        recordings_dir,
        prefix,
        suffix,
        "client_response",
        {
            "status_code": 200,
            "headers": {"content-type": "application/json"},
            "body": response_body,
        },
    )

    result = validate_recording(prefix, suffix, recordings_dir)
    assert result.passed is True
    assert result.errors == []
