"""录制数据校验脚本 - 验证录制数据的完整性和正确性"""
import json
import sys
from pathlib import Path
from typing import Optional
from dataclasses import dataclass

from tests.helpers.response_parsing import normalize_text, parse_nonstream_body, parse_sse_chunks


@dataclass
class ValidationResult:
    """校验结果"""
    prefix: str
    suffix: str
    passed: bool
    errors: list[str]
    warnings: list[str]


def load_recording(prefix: str, suffix: str, recordings_dir: Path = Path("recordings")) -> dict:
    """加载某个录制的所有文件

    文件格式: recordings/{prefix}__{suffix}__{type}.json
    """
    filename_prefix = f"{prefix}__{suffix}"

    files = {
        "client_request": recordings_dir / f"{filename_prefix}__client_request.json",
        "client_response": recordings_dir / f"{filename_prefix}__client_response.json",
        "backend_request": recordings_dir / f"{filename_prefix}__backend_request.json",
        "backend_response": recordings_dir / f"{filename_prefix}__backend_response.json",
    }

    data = {}
    for name, filepath in files.items():
        if filepath.exists():
            with open(filepath, 'r', encoding='utf-8') as f:
                data[name] = json.load(f)
        else:
            data[name] = None

    return data


def extract_content_from_chunks(chunks: list[str]) -> tuple[Optional[str], list[str]]:
    """从 SSE chunks 中提取 content"""
    content_parts = []
    errors = []

    for i, chunk in enumerate(chunks):
        # 跳过非 data: 开头的 chunk（如 : OPENROUTER PROCESSING）
        if not chunk.startswith("data: "):
            continue

        content = chunk[6:].strip()
        if content == "[DONE]":
            continue

        try:
            data = json.loads(content)
            delta = data.get("choices", [{}])[0].get("delta", {})
            if "content" in delta and delta["content"]:
                content_parts.append(delta["content"])
        except json.JSONDecodeError as e:
            errors.append(f"Chunk {i}: JSON 解析失败 - {e}")
        except Exception as e:
            errors.append(f"Chunk {i}: 提取 content 失败 - {e}")

    return "".join(content_parts) if content_parts else None, errors


def extract_content_from_body(body: dict) -> Optional[str]:
    """从非流式响应 body 中提取 content"""
    try:
        choices = body.get("choices", [{}])
        if choices:
            message = choices[0].get("message", {})
            return message.get("content")
    except:
        pass
    return None


def extract_reasoning_from_chunks(chunks: list[str]) -> tuple[Optional[str], list[str]]:
    """从 SSE chunks 中提取 reasoning_content"""
    reasoning_parts = []
    errors = []

    for i, chunk in enumerate(chunks):
        if not chunk.startswith("data: "):
            continue
        content = chunk[6:].strip()
        if content == "[DONE]":
            continue

        try:
            data = json.loads(content)
            delta = data.get("choices", [{}])[0].get("delta", {})
            if "reasoning_content" in delta:
                reasoning_parts.append(delta["reasoning_content"])
        except:
            pass

    # 过滤掉 None 值
    reasoning_parts = [p for p in reasoning_parts if p is not None]
    return "".join(reasoning_parts) if reasoning_parts else None, errors


def validate_sse_format(chunks: list[str]) -> tuple[bool, list[str]]:
    """验证 SSE 格式是否正确

    注意：某些后端（如 OpenRouter）会返回非标准的 chunk（如 : OPENROUTER PROCESSING），
    这些不是错误，只是元数据。
    """
    errors = []

    for i, chunk in enumerate(chunks):
        # 跳过非 data: 开头的 chunk（如 : OPENROUTER PROCESSING）
        if not chunk.startswith("data: "):
            continue

        content = chunk[6:].strip()
        if content == "[DONE]":
            continue

        try:
            json.loads(content)
        except json.JSONDecodeError as e:
            errors.append(f"Chunk {i}: JSON 解析失败 - {e}")

    return len(errors) == 0, errors


def validate_streaming_response(recording: dict) -> ValidationResult:
    """校验流式响应"""
    prefix = recording["client_request"]["prefix"]
    suffix = recording["client_request"]["suffix"]
    errors = []
    warnings = []

    client_response = recording.get("client_response")
    backend_response = recording.get("backend_response")
    backend_headers = backend_response.get("headers", {}) if backend_response else {}
    client_headers = client_response.get("headers", {}) if client_response else {}

    if not client_response or not backend_response:
        return ValidationResult(
            prefix=prefix,
            suffix=suffix,
            passed=False,
            errors=["缺少 client_response 或 backend_response"],
            warnings=[]
        )

    if client_response.get("status_code") != 200:
        warnings.append(f"客户端响应状态码异常: {client_response.get('status_code')}")

    if backend_response.get("status_code") != 200:
        errors.append(f"后端响应状态码异常: {backend_response.get('status_code')}")

    client_chunks = client_response.get("chunks", [])
    backend_chunks = backend_response.get("chunks", [])

    if not backend_chunks:
        errors.append("后端响应没有 chunks（流式数据）")
        return ValidationResult(prefix=prefix, suffix=suffix, passed=False, errors=errors, warnings=warnings)

    # 验证 SSE 格式
    sse_valid, sse_errors = validate_sse_format(backend_chunks)
    if not sse_valid:
        errors.extend([f"后端 SSE 格式错误: {e}" for e in sse_errors])

    # 检查 [DONE] 消息
    has_done = any("[DONE]" in chunk for chunk in backend_chunks)
    if not has_done:
        warnings.append("后端响应缺少 [DONE] 消息")

    if not backend_headers.get("content-type"):
        warnings.append("后端响应缺少 content-type header")
    if client_headers and not client_headers.get("content-type"):
        warnings.append("客户端响应缺少 content-type header")

    backend_parsed = parse_sse_chunks(backend_chunks)
    client_parsed = parse_sse_chunks(client_chunks) if client_chunks else None

    if backend_parsed["content"] and client_parsed and normalize_text(backend_parsed["content"]) != normalize_text(client_parsed["content"]):
        errors.append(
            f"content 内容不一致:\n  后端: {normalize_text(backend_parsed['content'])[:100]}...\n  客户端: {normalize_text(client_parsed['content'])[:100]}..."
        )

    if backend_parsed["reasoning_content"] and client_parsed and normalize_text(backend_parsed["reasoning_content"]) != normalize_text(client_parsed["reasoning_content"]):
        warnings.append("reasoning_content 与客户端响应不一致（可能被转换器重写）")

    if backend_parsed["tool_calls"] and client_parsed and len(backend_parsed["tool_calls"]) != len(client_parsed["tool_calls"]):
        errors.append("tool_calls 数量不一致")

    for tool_call in backend_parsed["tool_calls"]:
        if not tool_call["function"]["name"]:
            errors.append("存在缺少 function.name 的 tool_call")
        if tool_call["function"]["arguments"] is None:
            errors.append("存在缺少 function.arguments 的 tool_call")

    valid_finish_reasons = {"stop", "tool_calls", "length", "content_filter", "function_call"}
    for finish_reason in backend_parsed["finish_reasons"]:
        if finish_reason not in valid_finish_reasons:
            warnings.append(f"发现未知 finish_reason: {finish_reason}")

    if backend_parsed["non_data_lines"]:
        warnings.append(f"后端包含 {len(backend_parsed['non_data_lines'])} 条非 data 注释/元数据行")

    if backend_chunks and "[DONE]" not in backend_chunks[-1]:
        warnings.append("[DONE] 不在流式响应尾部")

    # 检查 chunks 数量
    if client_chunks and len(client_chunks) < len(backend_chunks):
        warnings.append(f"客户端 chunks 数量 ({len(client_chunks)}) 少于后端 ({len(backend_chunks)})，可能是流式被截断")

    return ValidationResult(
        prefix=prefix,
        suffix=suffix,
        passed=len(errors) == 0,
        errors=errors,
        warnings=warnings
    )


def validate_non_streaming_response(recording: dict) -> ValidationResult:
    """校验非流式响应"""
    prefix = recording["client_request"]["prefix"]
    suffix = recording["client_request"]["suffix"]
    errors = []
    warnings = []

    client_response = recording.get("client_response")
    backend_response = recording.get("backend_response")

    if not client_response or not backend_response:
        return ValidationResult(
            prefix=prefix,
            suffix=suffix,
            passed=False,
            errors=["缺少 client_response 或 backend_response"],
            warnings=[]
        )

    if client_response.get("status_code") != 200:
        errors.append(f"客户端响应状态码异常: {client_response.get('status_code')}")

    if backend_response.get("status_code") != 200:
        errors.append(f"后端响应状态码异常: {backend_response.get('status_code')}")

    client_body = client_response.get("body")
    backend_body = backend_response.get("body")
    backend_headers = backend_response.get("headers", {})
    client_headers = client_response.get("headers", {})

    if not backend_body:
        errors.append("后端响应没有 body")
        return ValidationResult(prefix=prefix, suffix=suffix, passed=False, errors=errors, warnings=warnings)

    if not backend_headers.get("content-type"):
        warnings.append("后端响应缺少 content-type header")
    if client_headers and not client_headers.get("content-type"):
        warnings.append("客户端响应缺少 content-type header")

    required_fields = ["id", "choices"]
    for field in required_fields:
        if field not in backend_body:
            errors.append(f"后端响应缺少必要字段: {field}")

    backend_parsed = parse_nonstream_body(backend_body)
    client_parsed = parse_nonstream_body(client_body) if client_body else None

    if client_parsed and normalize_text(backend_parsed["content"]) != normalize_text(client_parsed["content"]):
        errors.append(
            f"content 内容不一致:\n  后端: {normalize_text(backend_parsed['content'])[:100]}...\n  客户端: {normalize_text(client_parsed['content'])[:100]}..."
        )

    if client_parsed and normalize_text(backend_parsed["reasoning_content"]) != normalize_text(client_parsed["reasoning_content"]):
        warnings.append("reasoning_content 与客户端响应不一致（可能被过滤或转换）")

    for tool_call in backend_parsed["tool_calls"]:
        if not tool_call["function"]["name"]:
            errors.append("存在缺少 function.name 的 tool_call")
        if tool_call["function"]["arguments"] is None:
            errors.append("存在缺少 function.arguments 的 tool_call")

    valid_finish_reasons = {"stop", "tool_calls", "length", "content_filter", "function_call"}
    for finish_reason in backend_parsed["finish_reasons"]:
        if finish_reason not in valid_finish_reasons:
            warnings.append(f"发现未知 finish_reason: {finish_reason}")

    return ValidationResult(
        prefix=prefix,
        suffix=suffix,
        passed=len(errors) == 0,
        errors=errors,
        warnings=warnings
    )


def validate_request_mapping(recording: dict) -> ValidationResult:
    """校验请求映射关系"""
    prefix = recording["client_request"]["prefix"]
    suffix = recording["client_request"]["suffix"]
    errors = []
    warnings = []

    client_request = recording.get("client_request")
    backend_request = recording.get("backend_request")

    if not client_request or not backend_request:
        return ValidationResult(
            prefix=prefix,
            suffix=suffix,
            passed=False,
            errors=["缺少 client_request 或 backend_request"],
            warnings=[]
        )

    if client_request.get("method") != backend_request.get("method"):
        errors.append(f"HTTP method 不匹配: client={client_request.get('method')}, backend={backend_request.get('method')}")

    client_model = client_request.get("body", {}).get("model")
    backend_model = backend_request.get("body", {}).get("model")

    if client_model and backend_model:
        if client_model == backend_model:
            warnings.append("model 字段未被替换（可能配置问题）")
    if client_request.get("endpoint") != backend_request.get("endpoint"):
        warnings.append(
            f"client/backend endpoint 不同: client={client_request.get('endpoint')}, backend={backend_request.get('endpoint')}"
        )

    client_headers = client_request.get("headers", {})
    backend_headers = backend_request.get("headers", {})
    if "content-type" not in {key.lower() for key in client_headers.keys()}:
        errors.append("client_request 缺少 content-type header")
    if "content-type" not in {key.lower() for key in backend_headers.keys()}:
        errors.append("backend_request 缺少 content-type header")

    return ValidationResult(
        prefix=prefix,
        suffix=suffix,
        passed=len(errors) == 0,
        errors=errors,
        warnings=warnings
    )


def validate_recording(prefix: str, suffix: str, recordings_dir: Path = Path("recordings")) -> ValidationResult:
    """校验单个录制"""
    recording = load_recording(prefix, suffix, recordings_dir)

    all_errors = []
    all_warnings = []

    req_result = validate_request_mapping(recording)
    all_errors.extend(req_result.errors)
    all_warnings.extend(req_result.warnings)

    backend_response = recording.get("backend_response")
    if backend_response:
        if backend_response.get("chunks"):
            resp_result = validate_streaming_response(recording)
            all_errors.extend(resp_result.errors)
            all_warnings.extend(resp_result.warnings)
        elif backend_response.get("body"):
            resp_result = validate_non_streaming_response(recording)
            all_errors.extend(resp_result.errors)
            all_warnings.extend(resp_result.warnings)
        elif backend_response.get("error"):
            all_errors.append(f"后端响应错误: {backend_response.get('error')}")
        else:
            all_warnings.append("后端响应没有 body/chunks/error")

    return ValidationResult(
        prefix=prefix,
        suffix=suffix,
        passed=len(all_errors) == 0,
        errors=all_errors,
        warnings=all_warnings
    )


def list_recordings(recordings_dir: Path = Path("recordings")) -> list[tuple[str, str]]:
    """列出所有录制

    Returns:
        [(prefix, suffix), ...]
    """
    if not recordings_dir.exists():
        return []

    recordings = set()
    # 文件格式: {prefix}__{suffix}__{type}.json
    for file in recordings_dir.glob("*__*__*.json"):
        name = file.stem  # 去掉 .json
        # 从后往前：prefix__suffix__type
        parts = name.rsplit("__", 2)
        if len(parts) == 3:
            prefix = parts[0]
            suffix = parts[1]
            recordings.add((prefix, suffix))

    return sorted(list(recordings))


def main():
    """主函数"""
    recordings_dir = Path("recordings")

    if not recordings_dir.exists():
        print(f"错误: 录制目录不存在: {recordings_dir}")
        sys.exit(1)

    recordings = list_recordings(recordings_dir)

    if not recordings:
        print("警告: 没有找到录制文件")
        sys.exit(0)

    print(f"找到 {len(recordings)} 个录制\n")

    total_errors = 0
    total_warnings = 0

    for prefix, suffix in recordings:
        result = validate_recording(prefix, suffix, recordings_dir)

        status = "✓ PASS" if result.passed else "✗ FAIL"
        print(f"{status}: {prefix} ({suffix})")

        for error in result.errors:
            print(f"  错误: {error}")
            total_errors += 1

        for warning in result.warnings:
            print(f"  警告: {warning}")
            total_warnings += 1

        print()

    print("=" * 50)
    print(f"总计: {len(recordings)} 个录制")
    print(f"错误: {total_errors}")
    print(f"警告: {total_warnings}")

    if total_errors > 0:
        sys.exit(1)
    elif total_warnings > 0:
        sys.exit(2)
    else:
        sys.exit(0)


if __name__ == "__main__":
    main()
