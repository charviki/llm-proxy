import pytest
import json
from proxy.stream import StreamSimulator

@pytest.mark.asyncio
async def test_simulate_chat_completion():
    response_json = {
        "choices": [
            {
                "message": {
                    "content": "Hello",
                    "reasoning_content": "thinking"
                }
            }
        ]
    }
    model_id = "test-model"
    
    generator = StreamSimulator.simulate_chat_completion(response_json, model_id)
    chunks = [chunk async for chunk in generator]
    
    # Verify chunks
    # 1. role chunk
    assert b'"role":"assistant"' in chunks[0]
    # 2. reasoning chunk ("thin", "king") -> len 8, 4 per chunk = 2 chunks
    # 3. content chunk ("Hell", "o") -> len 5, 4 per chunk = 2 chunks
    # 4. stop chunk
    # 5. [DONE]
    
    decoded_chunks = [c.decode('utf-8') for c in chunks]
    
    # Check reasoning chunks
    assert '"reasoning_content":"thin"' in decoded_chunks[1]
    assert '"reasoning_content":"king"' in decoded_chunks[2]
    
    # Check content chunks
    assert '"content":"Hell"' in decoded_chunks[3]
    assert '"content":"o"' in decoded_chunks[4]
    
    # Check finish reason
    assert '"finish_reason":"stop"' in decoded_chunks[5]
    assert "[DONE]" in decoded_chunks[6]

@pytest.mark.asyncio
async def test_simulate_completions():
    response_json = {
        "choices": [
            {
                "text": "Hello"
            }
        ]
    }
    model_id = "test-model"
    
    generator = StreamSimulator.simulate_completions(response_json, model_id)
    chunks = [chunk async for chunk in generator]
    
    decoded_chunks = [c.decode('utf-8') for c in chunks]
    
    # 1. initial chunk
    assert '"text":""' in decoded_chunks[0]
    
    # 2. content chunks
    assert '"text":"Hell"' in decoded_chunks[1]
    assert '"text":"o"' in decoded_chunks[2]
    
    # 3. stop chunk
    assert '"finish_reason":"stop"' in decoded_chunks[3]
    assert "[DONE]" in decoded_chunks[4]
