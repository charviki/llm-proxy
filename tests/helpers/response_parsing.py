import json
import re
from typing import Optional


def normalize_text(text: Optional[str]) -> str:
    if not text:
        return ""
    text = text.replace("\n", " ")
    text = re.sub(r"\s+", " ", text)
    return text.strip()


def strip_think_tags(content: Optional[str]) -> str:
    if not content:
        return ""
    cleaned_content = re.sub(r"<think>.*?</think>", "", content, flags=re.DOTALL)
    return cleaned_content.strip()


def extract_think_reasoning(content: Optional[str]) -> str:
    if not content:
        return ""
    think_matches = re.findall(r"<think>\s*(.*?)\s*</think>", content, re.DOTALL)
    return "\n".join(think_matches).strip() if think_matches else ""


def _merge_tool_call(tool_calls_map: dict[int, dict], tool_call_delta: dict) -> None:
    index = tool_call_delta.get("index", 0)
    tool_call = tool_calls_map.setdefault(
        index,
        {
            "_index": index,
            "id": "",
            "type": "function",
            "function": {"name": "", "arguments": ""},
        },
    )

    if tool_call_delta.get("id"):
        tool_call["id"] = tool_call_delta["id"]

    function_delta = tool_call_delta.get("function", {})
    if function_delta.get("name"):
        tool_call["function"]["name"] = function_delta["name"]
    if function_delta.get("arguments"):
        tool_call["function"]["arguments"] += function_delta["arguments"]


def parse_sse_chunks(chunks: list[str]) -> dict:
    content_parts: list[str] = []
    reasoning_parts: list[str] = []
    tool_calls_map: dict[int, dict] = {}
    finish_reasons: list[str] = []
    non_data_lines: list[str] = []
    done_seen = False

    for chunk in chunks:
        if not chunk:
            continue

        if not chunk.startswith("data: "):
            non_data_lines.append(chunk)
            continue

        data_str = chunk[6:].strip()
        if data_str == "[DONE]":
            done_seen = True
            continue

        try:
            data = json.loads(data_str)
        except json.JSONDecodeError:
            continue

        choices = data.get("choices", [])
        if not choices:
            continue

        choice = choices[0]
        delta = choice.get("delta", {})

        if delta.get("content"):
            content_parts.append(delta["content"])
        if delta.get("reasoning_content"):
            reasoning_parts.append(delta["reasoning_content"])
        elif delta.get("reasoning"):
            reasoning_parts.append(delta["reasoning"])

        for tool_call_delta in delta.get("tool_calls", []):
            _merge_tool_call(tool_calls_map, tool_call_delta)

        finish_reason = choice.get("finish_reason")
        if finish_reason:
            finish_reasons.append(finish_reason)

    raw_content = "".join(content_parts)
    explicit_reasoning = "".join(reasoning_parts)
    reasoning_content = explicit_reasoning or extract_think_reasoning(raw_content)

    tool_calls = list(tool_calls_map.values())
    for tool_call in tool_calls:
        tool_call.pop("_index", None)

    return {
        "raw_content": raw_content,
        "content": strip_think_tags(raw_content),
        "reasoning_content": reasoning_content,
        "tool_calls": tool_calls,
        "finish_reasons": finish_reasons,
        "done_seen": done_seen,
        "non_data_lines": non_data_lines,
    }


def parse_nonstream_body(response_json: dict) -> dict:
    choices = response_json.get("choices", [])
    choice = choices[0] if choices else {}
    message = choice.get("message", {})
    raw_content = message.get("content", "") or ""
    explicit_reasoning = message.get("reasoning_content") or message.get("reasoning") or ""

    tool_calls = []
    for tool_call in message.get("tool_calls", []):
        tool_calls.append(
            {
                "id": tool_call.get("id", ""),
                "type": tool_call.get("type", "function"),
                "function": {
                    "name": tool_call.get("function", {}).get("name", ""),
                    "arguments": tool_call.get("function", {}).get("arguments", ""),
                },
            }
        )

    finish_reason = choice.get("finish_reason")

    return {
        "raw_content": raw_content,
        "content": strip_think_tags(raw_content),
        "reasoning_content": explicit_reasoning or extract_think_reasoning(raw_content),
        "tool_calls": tool_calls,
        "finish_reasons": [finish_reason] if finish_reason else [],
        "done_seen": False,
        "non_data_lines": [],
    }


def collect_chunks_from_sse_body(body: bytes) -> list[str]:
    return [line for line in body.decode("utf-8").splitlines() if line]
