<div align="center">

# llm-proxy

**专为大语言模型 (LLM) API 设计的 MITM (中间人) HTTPS 代理网关**

<p align="center">
  简体中文 | <a href="README_en.md">English</a>
</p>

</div>

> [!IMPORTANT]
> **📝 免责声明**
>
> - 本项目仅供个人学习使用，不保证稳定性，且不提供任何技术支持。
> - 使用者必须在遵循 OpenAI 的 [使用条款](https://openai.com/policies/terms-of-use) 以及**法律法规**的情况下使用，不得用于非法用途。
> - 根据 [《生成式人工智能服务管理暂行办法》](http://www.cac.gov.cn/2023-07/13/c_1690898327029107.htm) 的要求，请勿对中国地区公众提供一切未经备案的生成式人工智能服务。

## 💡 为什么需要 llm-proxy？

许多现代 AI 应用（如某些客户端、IDE 插件等）在代码中硬编码了官方的模型 API 地址（例如 `api.openai.com`），并不允许用户自定义 Base URL 和私有模型。

`llm-proxy` 通过自签发 CA 证书并配合 DNS/Hosts 劫持，作为中间人拦截这些封闭应用的 HTTPS 请求。它在解密请求后，将其无缝转发到你配置的任何 LLM 后端服务（例如 OpenRouter、内部私有模型 API 等），从而**打破客户端的应用限制，让你能够自由使用自定义的强大模型**。

## ✨ 主要功能

- **HTTPS MITM 代理与证书自动签发**：自动生成自定义 CA 证书，并根据配置文件动态劫持目标域名（如 `api.openai.com`）。
- **完全兼容 OpenAI 协议**：本项目完全基于标准 OpenAI API 协议设计，客户端的请求格式和后端提供的 API 接口均需遵循 OpenAI 格式规范。
- **统一 API 访问与路由转发**：将标准请求无缝转发至不同的支持 OpenAI 协议的 LLM 供应商或自定义后端。
- **无感知的鉴权替换 (API Key 注入)**：客户端可以使用任意伪造的 API Key，代理会在转发请求时，根据模型路由规则自动从环境变量中读取并注入真实的 API Key。这极大地方便了在不受信任或限制严格的客户端环境中使用私有模型。
- **灵活的模型路由与映射**：可以通过 `config.yml` 灵活定义请求的分发规则：
  - **前缀路由 (Group Routing)**：当客户端请求的模型名称带有特定前缀时（例如客户端请求 `provider-a/gpt-4o`），代理会剥离前缀，并将请求发送到配置中对应的服务提供商（Provider A）的 API 地址。这适合管理聚合类 API 服务。
  - **精确映射 (API Routing)**：允许将客户端请求的特定模型名称（例如 `my-custom-model-v1`）在代理层无缝转换为后端真实需要的模型名称（例如 `claude-3-5-sonnet`），并转发到指定的私有部署地址。适合为客户端“伪装”或“重命名”模型。
- **智能推理过程 (Reasoning) 解析**：内置强大的解析器，可适配不同模型的思考过程输出格式（提取特定的 `<think>` 标签或独立的 `reasoning` 字段等），并统一将其转化为标准 OpenAI 协议格式（如 `reasoning_content`）返回给客户端，确保客户端 UI 能够正确渲染思考过程。
- **流式模拟 (Stream Simulation)**：对于不支持流式输出的后端服务，代理可自动降级发送非流式请求，拿到完整响应后，通过 `StreamSimulator` 模拟成标准的 SSE 流式输出，完美兼容强制要求流式输入的客户端。
- **统一内部事件流架构**：原生 SSE 与非流式 JSON 会先统一转换为内部事件流，再分别进入流式处理器或非流式聚合器。这让流式清洗、语义合包与 JSON 聚合共享同一套核心处理链，显著降低双分支维护成本。
- **面向 SSE 的语义合包 (Semantic Coalescing)**：可按时间窗口与长度阈值合并连续 `content` 增量和同一 `tool_call` 的 `arguments` 增量，减少过碎 SSE 事件数量；默认关闭，可按环境逐步调优。
- **运行时流量录制与重放 (Traffic Recording & Replay)**：通过 FastAPI Middleware 和可插拔的 Transport 中间件链，实时录制客户端请求/响应以及后端请求/响应到 JSON 文件。支持通过发送携带 `X-Replay-Id` 的请求，在运行时无缝短路后端请求并重放录制的 Mock 响应，完美支持不消耗 Token 的隔离调试与回归测试。

## ⚙️ 原理与工作流

1. **证书劫持**：在宿主机安装 `llm-proxy` 生成的 CA 根证书并信任。
2. **DNS 欺骗**：修改系统的 `hosts` 文件，将目标域名（如 `api.openai.com`）指向代理服务器（如 `127.0.0.1`）。
3. **无缝转发**：客户端应用发送的请求会被代理截获，重新路由并修改参数后，发送给真实的自定义 LLM 服务。
4. **模型列表接管 (Model Discovery)**：许多客户端启动时会请求 `/v1/models` 获取可用模型。`llm-proxy` 会直接接管此请求，在服务启动时主动调用远端接口或读取本地缓存文件（如 `models/` 目录下）加载并合并所有支持的模型，最后将组合好的模型列表返回给客户端。
5. **统一响应处理**：对于 `chat/completions` 等核心接口，代理会先通过 `BackendClient` 把上游原生流式或非流式结果统一转换为内部事件流，再交给流式处理器输出 SSE，或交给响应聚合器输出 JSON。

---

## 📚 渐进式开发指引 (Documentation & Guidelines)

本项目针对开发者和 AI Agent 提供了**渐进式的文档导航**。在修改代码前，请务必阅读对应目录下的指引：

- 🏠 **[项目顶层开发指引 (AGENTS.md)](AGENTS.md)**：项目顶层架构、设计原则与全局开发规范。
- ⚙️ **[代理核心开发指引 (proxy/AGENTS.md)](proxy/AGENTS.md)**：核心路由、解析器、流式模拟器及性能开发规范。
- 🧪 **[Agent 测试指引 (tests/agent/README.md)](tests/agent/README.md)**：如何使用工具录制真实的 LLM 流量，如何通过自动化测试验证修改，以及本地重放的使用方法。

---

## 🚀 安装与部署

本项目支持 **源码启动** 和 **Docker Compose 部署** 两种方式。不管哪种方式，**都必须完成证书安装和 hosts 配置**。

### 准备工作 (必做)

> ⚠️ **环境依赖要求**
>
> 无论是本地运行还是通过 Docker 部署，在生成自签发证书时都依赖系统提供 `openssl` 命令行工具。请确保你的环境已安装它：
>
> - **macOS**: `brew install openssl`
> - **Ubuntu/Debian**: `sudo apt update && sudo apt install openssl`
> - **Windows**: 推荐通过 [Scoop](https://scoop.sh/) 安装 `scoop install openssl`

1. **克隆项目到本地**：
   ```bash
   git clone https://github.com/charviki/llm-proxy.git
   cd llm-proxy
   ```
2. 将项目根目录下的 `config.example.yml` 复制为 `config.yml`。
3. 根据你的实际后端服务，修改 `config.yml` 中的路由和模型映射规则。**（强烈建议阅读 `config.example.yml` 内的注释，其中包含了模型路由、解析器 `chunk_parsers` 等详尽的配置示例）**
4. 如果你希望减少过碎的 SSE 事件，可按需在 `config.yml` 中配置 `sse_coalescing.enabled / window_ms / max_buffer_length`；默认不启用，保持现有输出行为。
5. **(如果是 Docker 部署)**：将项目根目录下的 `docker-compose.example.yml` 复制为 `docker-compose.yml`。如果你在 `config.yml` 中配置了 `api_key_env`，需要在 `docker-compose.yml` 的 `environment` 节点中注入对应的真实 API Key（或者通过 `.env` 文件传递）。

### 方式一：源码启动 (本地开发)

本项目使用 [uv](https://github.com/astral-sh/uv) 进行依赖管理。

1. **安装依赖**：

   ```bash
   uv sync
   ```

2. **生成 CA 证书与服务端证书**：
   _在启动服务前，必须先生成证书！_

   ```bash
   uv run python -m cli cert
   ```

   这将在 `ca/` 目录下生成 `llm-proxy.crt` (公钥) 和 `llm-proxy.key` (私钥)。

3. **运行服务**：
   ```bash
   sudo uv run python main.py
   ```
   _注意：因为代理需要监听 `443` 标准 HTTPS 端口进行劫持，通常需要 `sudo` 管理员权限。_

### 方式二：Docker Compose 部署 (推荐)

使用 Docker 部署更加干净，且容器会自动为你生成证书。

#### 启动服务

1. **启动容器**：
   ```bash
   docker compose up -d --build
   ```
2. **提取生成的证书**：
   容器启动后，证书会自动生成并挂载到宿主机的 `./ca` 目录下。你需要获取 `./ca/llm-proxy-ca.crt` 文件。

---

## 🔧 客户端配置 (核心步骤)

要让代理生效，你**必须**在运行目标应用（如 IDE）的机器上完成以下两步配置：

### 1. 安装并信任 CA 证书

找到上面步骤生成的 `ca/llm-proxy-ca.crt` 文件（这是根证书），并将其安装到系统的受信任根证书颁发机构中。

> **💡 特别提示 (Windows + WSL 用户)**：如果你的 IDE 运行在 Windows 上，但通过 WSL (如 Ubuntu) 进行远程开发，由于 IDE 的底层网络请求极有可能直接从 WSL 环境中发出，你**必须在 Windows 宿主机和 WSL 子系统中都安装并信任该证书**。同时，除非你的 WSL 已经配置了与 Windows 宿主机共享 DNS/Hosts，否则**两边都需要修改 hosts 文件**！

- **macOS**:
  双击 `llm-proxy-ca.crt`，在 "钥匙串访问" 中找到它，右键 -> 显示简介 -> 展开 "信任" -> 将 "使用此证书时" 改为 **始终信任**。
- **Windows**:
  双击 `llm-proxy-ca.crt` -> 安装证书 -> 选择 **本地计算机** -> 将所有的证书都放入下列存储 -> 浏览 -> 选择 **受信任的根证书颁发机构**。
- **Linux**:
  根据发行版不同，通常将证书复制到 `/usr/local/share/ca-certificates/`，并运行 `sudo update-ca-certificates`。

### 2. 配置 Hosts 文件劫持域名

修改系统的 hosts 文件，将你需要劫持的域名（对应 `config.yml` 中的 `server.domains`）指向代理服务器的 IP 地址。

- **默认情况**：指向 `127.0.0.1`。
- **如遇本地 443 端口、VPN 或其他代理软件冲突**：可尝试让代理绑定到其他回环 IP，再把 hosts 指向对应地址。

编辑文件（macOS/Linux: `/etc/hosts`，Windows: `C:\Windows\System32\drivers\etc\hosts`），添加：

```text
# llm-proxy 劫持
127.0.0.1 api.openai.com
# 如果你改为绑定其他回环 IP，也可以改成对应地址，例如：
# 127.0.0.2 api.openai.com
```

完成后，目标应用对 `api.openai.com` 的所有请求都会被 `llm-proxy` 截获，并按照你的 `config.yml` 规则路由到真实的自定义模型上！

---

## ❓ 常见问题与特殊环境配置

### 解决 443 端口与 VPN 冲突（建议）

默认情况下，`docker-compose.yml` 中容器监听的是本地 `443` 端口 (`443:443`)。在大多数情况下，这样已经足够。

但是，这可能会与宿主机上运行的 Nginx 等其他 Web 服务产生端口冲突，或者**在开启某些 VPN 或代理软件（如 Clash、Surge 等）时，极易发生路由或端口接管冲突，导致无法正常劫持请求**。

更推荐的做法是：

1. 让代理直接绑定到一个未被占用的其他回环 IP。
2. 将 hosts 中对应域名改为该回环 IP。
3. 根据你的本机网络环境，自行选择合适的转发或监听方式。

例如，你可以尝试使用 `127.0.0.2`、`127.0.0.3` 等其他回环地址；关键原则是**代理监听地址、证书信任环境与 hosts 指向必须保持一致**。

### 规避 Trae + Windows + WSL 环境下的流式输出异常（乱序/中断）

在 **Trae + Windows + WSL** 的混合开发环境中，当客户端网络请求从 WSL 发出时，由于 WSL 的网络栈（基于 Hyper-V 虚拟机转换）特性，高频的流式数据可能会在网络层出现堆积。这会导致 **TCP 接收缓冲区中积累了多个 SSE 消息块（即发生 TCP 粘包）**，并被应用层一次性读取。

由于 Trae 客户端目前的 SSE 流式解析逻辑在应对这种底层粘包现象时存在一定的脆弱性（例如消息边界识别错误），往往会引发**内容乱序**或者误判为连接错误而导致**对话提前异常终止**的现象。

**工程规避方案**：
如果在该环境下遇到此类问题，可以通过开启 `llm-proxy` 的**服务端语义合包与延迟发送**功能来作为有效的工程规避手段。你需要在 `config.yml` 中启用 `sse_coalescing` 并增加 `processing_delay_ms` 参数（例如设置为 50~500 毫秒）。这会促使代理在服务端减少 SSE 消息的数量（降低网络微突发的概率），并在关键节点插入物理缓冲延时，从而减轻客户端脆弱解析器的压力：

```yaml
sse_coalescing:
  enabled: true
  window_ms: 20
  max_buffer_length: 256
  processing_delay_ms: 50  # 建议配置，在服务端强制进行语义合包和插入物理延时，规避 Trae 解析粘包的问题
```
