import json


def build_request_signature(request_body: dict) -> str:
    signature_messages = []
    for message in request_body.get("messages", []):
        normalized_message = {
            "role": message.get("role"),
            "content": message.get("content"),
        }
        if message.get("tool_calls"):
            normalized_message["tool_calls"] = [
                {
                    "id": tool_call.get("id"),
                    "name": tool_call.get("function", {}).get("name"),
                    "arguments": tool_call.get("function", {}).get("arguments"),
                }
                for tool_call in message["tool_calls"]
            ]
        if message.get("tool_call_id"):
            normalized_message["tool_call_id"] = message.get("tool_call_id")
        signature_messages.append(normalized_message)

    signature_payload = {
        "model": request_body.get("model"),
        "messages": signature_messages,
        "stream": request_body.get("stream"),
    }
    return json.dumps(signature_payload, sort_keys=True, ensure_ascii=False)
