# 代理核心模块开发指引

本目录 (`proxy/`) 包含了 `llm-proxy` 的核心业务逻辑，包括请求拦截、模型路由、数据转发以及响应格式转换。
在修改请求处理或响应清洗逻辑时，请参考本文档。

## 1. 代码关联与功能介绍

| 模块 / 文件 | 核心职责 |
| ----------- | -------- |
| **`models.py`** | **模型路由与发现 (`ModelsManager`)**：<br>1. **请求路由**: 匹配模型 ID，决定请求发往哪个后端端点并获取对应的 API Key。<br>2. **模型接管**: 启动时拉取并缓存模型列表，拦截 `/v1/models` 请求返回伪装列表。 |
| **`handler.py`** | **核心代理编排器 (`ProxyHandler`)**：<br>接管请求、修改模型 ID、注入鉴权头，协调 `BackendClient`、流式处理器与响应聚合器。当前职责应保持在“路由编排与最终响应装配”，避免重新膨胀成双主流程处理器。 |
| **`backend_client.py`** | **上游客户端适配层 (`BackendClient`)**：<br>位于 `ProxyHandler` 与 `httpx.AsyncClient` 之间，统一原生 SSE 与非流式 JSON 的内部表示，负责上游请求重试边界、原生流状态码语义以及非流式转内部事件流。 |
| **`converter.py`** | **响应清洗引擎**：<br>匹配对应的解析器，处理后端返回的不同格式。例如统一提取 `<think>` 标签、`reasoning` 字段或 `reasoning_content` 字段的内容，转换为标准的推理内容输出。<br>*(注：可通过 `config.yml` 中的 `chunk_parsers` 字段以“解析器 -> 关键词列表”的结构灵活配置解析规则与模型的绑定关系)* |
| **`stream.py`** | **流式模拟器 (`StreamSimulator`)**：<br>提供非流式响应到内部增量 payload / SSE 的模拟能力。当前更多作为统一事件流架构中的内部实现细节，而不是由 handler 直接分叉调度的主路径。 |
| **`stream_processor.py`** | **流式事件处理器 (`StreamEventProcessor`)**：<br>消费统一 SSE 事件，执行 converter 清洗、语义合包、边界 flush 和最终 SSE 编码。 |
| **`response_assembler.py`** | **非流式响应聚合器**：<br>消费统一事件流并重新组装为标准的 `chat/completions` 或 `completions` JSON 响应。 |
| **`transport.py`** | **代理传输层 (`ProxyTransport`)**：<br>包装 httpx 传输层，通过可插拔的 `Middleware` 链架构扩展功能。支持在请求发送前后进行短路或修改。 |
| **`recording_interceptor.py`** | **录制/重放中间件 (`TransportRecordingMiddleware` / `ReplayMiddleware`)**：<br>继承 `Middleware` 基类，处理后端流量的录制以及基于 header 的录制重放短路。 |
| **`middleware.py`** | **录制中间件 (`RecordingMiddleware`)**：<br>FastAPI 中间件，录制客户端请求/响应，并通过 contextvars 设置录制上下文供传输层录制/重放中间件使用。 |
| **`recorder.py`** | **录制核心逻辑**：<br>包含 contextvars 上下文管理、prefix 生成、JSON 文件写入等核心功能。 |

## 1.1 当前推荐的数据流

以 `chat/completions` 为例，当前核心链路应理解为：

1. `ProxyHandler` 负责模型路由、目标后端选择、请求改写与响应装配。
2. `BackendClient` 负责发起上游请求，并把：
   - 原生 `text/event-stream`
   - 非流式 JSON
   统一转成内部事件流。
3. 流式客户端路径由 `StreamEventProcessor` 消费统一事件流，依次执行：
   - converter 清洗
   - 语义合包
   - SSE 编码
4. 非流式客户端路径由 `response_assembler.py` 消费同一套事件流，再聚合回 JSON。

这意味着新增流式语义能力时，优先考虑接在“统一事件流之后”，而不是在 handler 中复制原生流 / 模拟流两套逻辑。

## 2. 核心开发规范 (注重性能)

本项目作为中间件，对性能要求极高。在进行功能开发和修改时，必须遵循以下规范：

1. **极致性能导向**：
   - 流式响应（SSE）会产生大量细碎的 Chunk，在解析和转换时必须最小化字符串查找和字典重组的开销。
   - 优先采用轻量级的状态标记或简单的字符串操作，避免在热点代码路径（如每个 Chunk 的处理逻辑）中引入复杂的解析或深度拷贝。
2. **统一的日志记录 (Logger Injection)**：
   - **禁止**在模块内部使用硬编码获取 logger (如 `logging.getLogger("llm_proxy")` 或 `logging.getLogger(__name__)`)。
   - 所有类（包括 FastAPI 中间件、传输层 Middleware 等）的 logger 必须通过**外部构造函数（__init__）依赖注入**传入，并且**必须是必传参数（不可设为可选或使用 `logger=None` 并内部兜底）**，确保日志配置的统一控制和可测试性。
   - 静态方法如果需要日志记录，可以考虑转为实例方法或作为参数传入 logger。
3. **逻辑一致性**：
   - 修改 `models.py` 的路由逻辑时，需确保前缀剥离和精确映射功能不受损。
   - 修改 `converter.py` 的清洗逻辑时，请同步检查统一事件流的两条消费者：`stream_processor.py` 与 `response_assembler.py`；其中 `reasoning` 类型解析器现在表示“通用 reasoning 字段解析”，不要再按 Gemini 专属语义命名新逻辑。
   - 修改 `backend_client.py` 时，必须同时检查原生流状态码语义、多行 SSE 兼容性与非流式模拟异常兜底。
   - 修改 `transport.py` / `middleware.py` 时，不要把 OpenAI 协议语义、tool_calls 聚合或非流式转流式策略下沉进通用传输层。
4. **测试保障**：
   - 任何改动后必须运行 `pytest tests/test_converter.py` 验证基础逻辑。
   - 修改统一事件流相关逻辑后，至少补跑 `pytest tests/test_backend_client.py tests/test_stream_processor.py tests/test_response_assembler.py tests/test_handler.py`。
   - 涉及流式解析和转换的改动，必须运行 `pytest tests/agent/` 验证基于真实录制数据的端到端解析能力。
