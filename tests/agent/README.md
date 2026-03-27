# Agent 集成测试

## 概述

验证 llm-proxy 的**组装逻辑**在代码改动后不受影响：
- Reasoning Content 完整性
- Tool Calls 字段不丢失
- 流式 Chunk 组装顺序正确

## 核心思路：录制-回放

```
录制阶段（一次性）
├── 直接调用真实模型 API
├── 保存后端原始返回（raw_chunks）
└── 保存预处理结果（expected 结果）

回放阶段（日常测试）
├── Mock 后端返回录制的原始 raw_chunks
├── 通过完整 llm-proxy 处理链路
├── 对比最终输出 vs expected 结果
└── 不一致 → llm-proxy 组装逻辑损坏
```

## 目录结构

```
tests/agent/
├── mock_data/                              # 录制的 mock 数据
│   ├── project_analysis__stream_think/     # MiniMax think_tag 流式
│   ├── project_analysis__nonstream_think/   # MiniMax think_tag 非流式
│   ├── project_analysis__stream_reasoning/ # OpenRouter reasoning 流式
│   └── project_analysis__nonstream_reasoning/ # OpenRouter reasoning 非流式
├── fixtures/
│   └── mock_server.py                      # Mock 后端服务器
├── record_agent_workflow.py                # 录制脚本
└── test_agent_workflow.py                  # 测试用例
```

## 支持的 Reasoning 格式

| 格式 | 特征 | Parser 配置 |
|------|------|-------------|
| think_tag | content 中包含 `<think>...</think>` 标签 | `think_tag` |
| reasoning | delta 中有独立的 `reasoning` 字段 | `reasoning` |
| reasoning_content | delta 中有独立的 `reasoning_content` 字段 | `reasoning_content` |

## 数据流程

### 录制阶段
```
后端模型 → 原始 SSE chunks（包含 think 标签）
  ↓
录制脚本收集 raw_chunks
  ↓
录制脚本预处理 → 提取 content（去除 think）、reasoning_content
  ↓
保存：raw_chunks + expected_content + expected_reasoning_content
```

### 测试阶段
```
Mock Server → 返回原始 raw_chunks（重组为非流式 JSON）
  ↓
llm-proxy → 根据配置的 parser 处理，返回 SSE 格式
  ↓
测试脚本 → 对 llm-proxy 输出进行同样的预处理
  ↓
对比：实际预处理结果 vs 录制时预处理结果
```

## 录制 Mock 数据

### 环境准备
```bash
# 设置 API Key（根据你的模型提供商）
export <YOUR_API_KEY_ENV>=your_key_here
# 例如：OPENAI_API_KEY, GEMINI_API_KEY, DEEPSEEK_API_KEY 等
```

### 流式录制
```bash
python tests/agent/record_agent_workflow.py \
    --output tests/agent/mock_data/<test_name>__stream \
    --backend-endpoint https://api.example.com/v1 \
    --backend-api-key $YOUR_API_KEY_ENV \
    --backend-model your-model-id \
    --path /path/to/project
```

### 非流式录制
```bash
python tests/agent/record_agent_workflow.py \
    --output tests/agent/mock_data/<test_name>__nonstream \
    --backend-endpoint https://api.example.com/v1 \
    --backend-api-key $YOUR_API_KEY_ENV \
    --backend-model your-model-id \
    --path /path/to/project \
    --stream false
```

## 新增测试步骤

### Step 1: 确定模型和 API
- **API Endpoint**：模型提供的 API 地址
- **API Key**：环境变量名
- **模型 ID**：如 `gpt-4`、`claude-3`、`gemini-pro` 等

### Step 2: 确定 Reasoning 格式
根据模型返回格式选择合适的 parser

### Step 3: 录制 Mock 数据
```bash
# 示例：录制任意模型
export YOUR_API_KEY=your_key
python tests/agent/record_agent_workflow.py \
    --output tests/agent/mock_data/project_analysis__stream \
    --backend-endpoint https://api.example.com/v1 \
    --backend-api-key $YOUR_API_KEY \
    --backend-model your-model-id \
    --path /path/to/project
```

### Step 4: 添加测试用例

在 `tests/agent/test_agent_workflow.py` 中添加：

```python
@pytest.mark.asyncio
async def test_project_analysis_<format>(mock_logger, parser_matcher):
    """
    验证 xxx 模型 xxx 格式的处理逻辑
    """
    _skip_if_no_reasoning_mock_data()  # 或对应的 skip 函数

    # 根据格式选择合适的 parser
    format_matcher = ChunkConverterMatcher({
        "model_keyword": "<parser_type>",  # think_tag / reasoning / reasoning_content
        "default": "reasoning_content"
    })

    mock_data = load_mock_data("<test_name>__stream")

    mock_server = create_mock_server(mock_data, stream=False)

    backends_config = BackendsConfig(
        groups=[],
        apis=[
            APIConfig(
                name="Mock Backend",
                endpoint="http://mock-backend",
                stream=False,
                custom_model_id="model-keyword-test",  # 必须包含关键词以匹配 parser
                target_model_id="mock-model"
            )
        ]
    )

    handler = ProxyHandler(backends_config, mock_logger, format_matcher)

    # ... 测试逻辑（参考现有测试）
```

### Step 5: 运行测试
```bash
# 运行单个测试
python -m pytest tests/agent/test_agent_workflow.py::test_project_analysis_<format> -v

# 运行所有 agent 测试
python -m pytest tests/agent/ -v
```

## 注意事项

### Parser 关键词匹配
请求中的 `custom_model_id` 必须包含配置文件中的关键词：
- 配置 `{"minimax": "think_tag"}`
- 请求 `custom_model_id="minimax-project-analyzer"` ✓
- 请求 `custom_model_id="project-analyzer"` ✗

### Mock 数据命名规范
- 流式：`<name>__stream_<format>`
- 非流式：`<name>__nonstream_<format>`

## 录制数据格式

```json
{
  "version": "1.0",
  "workflow": {
    "input": {"path": "/path/to/project"},
    "tools": [...],
    "model": "model-id"
  },
  "backend_responses": [
    {
      "step": 1,
      "raw_chunks": ["data: {...}", "data: {...}"],
      "reasoning_content": "提取的 reasoning",
      "content": "提取的 content"
    }
  ]
}
```

## 验证点

| 验证项 | 说明 |
|--------|------|
| `reasoning_content` | 思考内容完整 |
| `tool_calls[].name` | 函数名不丢失 |
| `tool_calls[].arguments` | 参数 JSON 完整 |
| `content` | 最终文本内容 |

## 当前可用测试

```bash
# 运行所有 agent 测试
python -m pytest tests/agent/ -v

# 输出示例
tests/agent/test_agent_workflow.py::test_project_analysis__full_loop PASSED
tests/agent/test_agent_workflow.py::test_mock_data_exists PASSED
tests/agent/test_agent_workflow.py::test_project_analysis__reasoning_field PASSED
```

## 运行时录制数据校验

录制数据保存在 `recordings/` 目录，可使用校验脚本验证完整性：

```bash
# 校验所有录制数据
python tests/agent/validate_recordings.py
```

### 校验内容

| 校验项 | 说明 |
|--------|------|
| SSE 格式 | chunks 是否正确以 `data: ` 开头 |
| [DONE] 消息 | 流式响应是否以 `[DONE]` 结尾 |
| 状态码 | client/backend 响应状态码 |
| 内容完整性 | content、reasoning_content 是否丢失 |
| model 映射 | client/backend 请求的 model 字段是否正确 |
| chunks 数量 | 客户端 chunks 是否少于后端（可能截断） |

### 返回码

- `0`: 所有录制通过
- `1`: 存在错误
- `2`: 存在警告（但无错误）
