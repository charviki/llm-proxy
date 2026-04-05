"""Pytest 配置和共享 fixtures"""
import sys
from pathlib import Path

project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))
sys.path.insert(0, str(Path(__file__).parent))


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
