"""流式响应模拟器 - 将非流式响应转换为流式响应"""
import json
from typing import AsyncGenerator


class StreamSimulator:
    """流式响应模拟器"""

    @staticmethod
    async def simulate_chat_completion(
        response_json: dict,
        model_id: str
    ) -> AsyncGenerator[bytes, None]:
        """模拟 chat completions 流式响应"""
        try:
            message = response_json["choices"][0]["message"]
            content = message.get("content") or ""
            reasoning_content = message.get("reasoning_content") or ""

            yield f'data: {{"id":"chatcmpl-simulated","object":"chat.completion.chunk","created":1,"model":"{model_id}","choices":[{{"index":0,"delta":{{"role":"assistant"}},"finish_reason":null}}]}}\n\n'.encode('utf-8')

            if reasoning_content:
                for i in range(0, len(reasoning_content), 4):
                    chunk = reasoning_content[i:i+4]
                    chunk_escaped = json.dumps(chunk)[1:-1]
                    yield f'data: {{"id":"chatcmpl-simulated","object":"chat.completion.chunk","created":1,"model":"{model_id}","choices":[{{"index":0,"delta":{{"reasoning_content":"{chunk_escaped}"}},"finish_reason":null}}]}}\n\n'.encode('utf-8')

            if content:
                for i in range(0, len(content), 4):
                    chunk = content[i:i+4]
                    chunk_escaped = json.dumps(chunk)[1:-1]
                    yield f'data: {{"id":"chatcmpl-simulated","object":"chat.completion.chunk","created":1,"model":"{model_id}","choices":[{{"index":0,"delta":{{"content":"{chunk_escaped}"}},"finish_reason":null}}]}}\n\n'.encode('utf-8')

            yield f'data: {{"id":"chatcmpl-simulated","object":"chat.completion.chunk","created":1,"model":"{model_id}","choices":[{{"index":0,"delta":{{}},"finish_reason":"stop"}}]}}\n\n'.encode('utf-8')
            yield b'data: [DONE]\n\n'

        except Exception as e:
            yield f'data: {{"error": "模拟流式响应失败: {str(e)}"}}\n\n'.encode('utf-8')

    @staticmethod
    async def simulate_completions(
        response_json: dict,
        model_id: str
    ) -> AsyncGenerator[bytes, None]:
        """模拟 completions 流式响应"""
        try:
            content = response_json.get("choices", [{}])[0].get("text", "")
            yield f'data: {{"id":"cmpl-simulated","object":"text_completion","created":1,"model":"{model_id}","choices":[{{"index":0,"text":"","finish_reason":null}}]}}\n\n'.encode('utf-8')

            for i in range(0, len(content), 4):
                chunk = content[i:i+4]
                chunk_escaped = json.dumps(chunk)[1:-1]
                yield f'data: {{"id":"cmpl-simulated","object":"text_completion","created":1,"model":"{model_id}","choices":[{{"index":0,"text":"{chunk_escaped}","finish_reason":null}}]}}\n\n'.encode('utf-8')

            yield f'data: {{"id":"cmpl-simulated","object":"text_completion","created":1,"model":"{model_id}","choices":[{{"index":0,"text":"","finish_reason":"stop"}}]}}\n\n'.encode('utf-8')
            yield b'data: [DONE]\n\n'

        except Exception as e:
            yield f'data: {{"error": "模拟流式响应失败: {str(e)}"}}\n\n'.encode('utf-8')
