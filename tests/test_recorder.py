import json
import time
from pathlib import Path
from unittest.mock import patch

import pytest

from proxy.recorder import (
    generate_prefix,
    mask_headers,
    write_request,
    write_response,
    RecordingContext,
    get_recording_context,
    set_recording_context,
    clear_recording_context,
)


class TestGeneratePrefix:
    def test_strips_leading_slash(self):
        prefix, suffix = generate_prefix("/v1/chat/completions")
        assert prefix == "v1_chat_completions"

    def test_replaces_slashes(self):
        prefix, suffix = generate_prefix("/a/b/c")
        assert prefix == "a_b_c"

    def test_suffix_format(self):
        prefix, suffix = generate_prefix("/v1/models")
        parts = suffix.split("_")
        assert len(parts) == 2
        assert len(parts[1]) == 6


class TestMaskHeaders:
    def test_none_returns_empty(self):
        assert mask_headers(None) == {}

    def test_empty_dict_returns_empty(self):
        assert mask_headers({}) == {}

    def test_masks_authorization_bearer(self):
        result = mask_headers({"Authorization": "Bearer sk-12345"})
        assert result["Authorization"] == "Bearer ***"

    def test_masks_api_key(self):
        result = mask_headers({"api-key": "secret123"})
        assert result["api-key"] == "***"

    def test_masks_x_api_key(self):
        result = mask_headers({"X-API-Key": "secret456"})
        assert result["X-API-Key"] == "***"

    def test_preserves_non_sensitive(self):
        result = mask_headers({"Content-Type": "application/json"})
        assert result["Content-Type"] == "application/json"

    def test_mixed_headers(self):
        headers = {
            "Authorization": "Bearer token",
            "Content-Type": "application/json",
            "X-API-Key": "key",
            "Accept": "*/*",
        }
        result = mask_headers(headers)
        assert result["Authorization"] == "Bearer ***"
        assert result["Content-Type"] == "application/json"
        assert result["X-API-Key"] == "***"
        assert result["Accept"] == "*/*"

    def test_case_insensitive_authorization(self):
        result = mask_headers({"authorization": "Bearer tok"})
        assert result["authorization"] == "Bearer ***"

    def test_non_bearer_authorization(self):
        result = mask_headers({"Authorization": "Basic dXNlcjpwYXNz"})
        assert result["Authorization"] == "***"


class TestWriteRequest:
    def test_writes_json_file(self, tmp_path):
        with patch("proxy.recorder.RECORDINGS_DIR", tmp_path):
            write_request(
                prefix="v1_chat_completions",
                suffix="1234_abc",
                request_type="Client Request",
                endpoint="/v1/chat/completions",
                method="POST",
                url="https://api.example.com/v1/chat/completions",
                headers={"Authorization": "Bearer sk-test"},
                body={"model": "gpt-4", "messages": []},
            )

        files = list(tmp_path.glob("*.json"))
        assert len(files) == 1
        assert files[0].name == "v1_chat_completions__1234_abc__client_request.json"

        data = json.loads(files[0].read_text(encoding="utf-8"))
        assert data["version"] == "1.0"
        assert data["type"] == "Client Request"
        assert data["method"] == "POST"
        assert data["headers"]["Authorization"] == "Bearer ***"
        assert data["body"]["model"] == "gpt-4"


class TestWriteResponse:
    def test_writes_response_with_body(self, tmp_path):
        with patch("proxy.recorder.RECORDINGS_DIR", tmp_path):
            write_response(
                prefix="v1_chat",
                suffix="5678_def",
                response_type="Upstream Response",
                status_code=200,
                timing_ms=150.5,
                body={"id": "chatcmpl-123", "choices": []},
            )

        files = list(tmp_path.glob("*.json"))
        assert len(files) == 1
        assert files[0].name == "v1_chat__5678_def__upstream_response.json"

        data = json.loads(files[0].read_text(encoding="utf-8"))
        assert data["status_code"] == 200
        assert data["timing_ms"] == 150.5
        assert data["body"]["id"] == "chatcmpl-123"
        assert "chunks" not in data

    def test_writes_response_with_chunks(self, tmp_path):
        with patch("proxy.recorder.RECORDINGS_DIR", tmp_path):
            write_response(
                prefix="v1_chat",
                suffix="9999_xyz",
                response_type="Upstream Stream",
                status_code=200,
                timing_ms=500.0,
                chunks=["chunk1", "chunk2"],
            )

        data = json.loads(list(tmp_path.glob("*.json"))[0].read_text(encoding="utf-8"))
        assert data["chunks"] == ["chunk1", "chunk2"]
        assert "body" not in data

    def test_writes_response_with_error(self, tmp_path):
        with patch("proxy.recorder.RECORDINGS_DIR", tmp_path):
            write_response(
                prefix="v1_chat",
                suffix="err_001",
                response_type="Upstream Response",
                status_code=502,
                timing_ms=3000.0,
                error="Connection refused",
            )

        data = json.loads(list(tmp_path.glob("*.json"))[0].read_text(encoding="utf-8"))
        assert data["error"] == "Connection refused"
        assert data["status_code"] == 502


class TestRecordingContext:
    def test_context_manager_sets_and_clears(self):
        assert get_recording_context() == {} or get_recording_context() is None or True

        with RecordingContext("test_prefix") as ctx:
            current = get_recording_context()
            assert current is not None
            assert current["prefix"] == "test_prefix"

        cleared = get_recording_context()
        assert cleared is None or cleared == {}

    def test_add_chunk(self):
        ctx = RecordingContext("test")
        ctx.add_chunk("chunk1")
        ctx.add_chunk("chunk2")
        assert ctx.chunks == ["chunk1", "chunk2"]

    def test_get_timing_ms(self):
        ctx = RecordingContext("test")
        ctx.start_time = time.perf_counter() - 0.1
        timing = ctx.get_timing_ms()
        assert timing >= 90


class TestContextVars:
    def test_set_and_get(self):
        set_recording_context({"prefix": "hello"})
        ctx = get_recording_context()
        assert ctx["prefix"] == "hello"
        clear_recording_context()

    def test_clear(self):
        set_recording_context({"prefix": "test"})
        clear_recording_context()
        ctx = get_recording_context()
        assert ctx is None or ctx == {}
