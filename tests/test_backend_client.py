import json
import logging
from unittest.mock import AsyncMock, MagicMock

import pytest

from proxy.backend_client import BackendClient, UpstreamSSEEvent


class MockStreamResponse:
    def __init__(self, lines: list[str], status_code: int = 200, read_bytes: bytes = b""):
        self._lines = lines
        self.status_code = status_code
        self._read_bytes = read_bytes

    async def aiter_lines(self):
        for line in self._lines:
            yield line

    async def aread(self):
        return self._read_bytes


class MockStreamContext:
    def __init__(self, response: MockStreamResponse):
        self.response = response

    async def __aenter__(self):
        return self.response

    async def __aexit__(self, exc_type, exc, tb):
        return False


async def _collect_events(events):
    items = []
    async for item in events:
        items.append(item)
    return items


@pytest.mark.asyncio
async def test_backend_client_native_stream_non_200_returns_json_response():
    client = MagicMock()
    client.stream.return_value = MockStreamContext(
        MockStreamResponse(lines=[], status_code=503, read_bytes=b'{"error":{"message":"backend unavailable"}}')
    )
    backend_client = BackendClient(client, logging.getLogger("test_logger"))

    response = await backend_client.request(
        req_json={"stream": True},
        headers={},
        target_url="https://api.test.com/v1/chat/completions",
        endpoint="chat/completions",
        requested_model_id="my-model",
        client_requested_stream=True,
        backend_supports_stream=True,
    )

    assert response.status_code == 503
    assert response.events is None
    assert response.json_body["error"]["message"] == "backend unavailable"


def test_upstream_sse_event_joins_multiline_data():
    event = UpstreamSSEEvent(event_lines=[
        "data: hello",
        "data: world",
    ])

    assert event.data_content() == "hello\nworld"


@pytest.mark.asyncio
async def test_backend_client_chat_simulation_emits_error_event_on_invalid_json_shape():
    client = AsyncMock()
    logger = logging.getLogger("test_logger")
    backend_client = BackendClient(client, logger)

    events = backend_client._simulate_upstream_events(
        response_json={"id": "bad", "choices": []},
        endpoint="chat/completions",
        requested_model_id="my-model",
    )
    items = await _collect_events(events)

    assert len(items) == 1
    assert isinstance(items[0], UpstreamSSEEvent)
    data_content = items[0].data_content()
    assert data_content is not None
    payload = json.loads(data_content)
    assert payload["error"]["code"] == "proxy_stream_simulation_error"
