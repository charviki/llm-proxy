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

## ⚙️ 原理与工作流

1. **证书劫持**：在宿主机安装 `llm-proxy` 生成的 CA 根证书并信任。
2. **DNS 欺骗**：修改系统的 `hosts` 文件，将目标域名（如 `api.openai.com`）指向代理服务器（如 `127.0.0.1`）。
3. **无缝转发**：客户端应用发送的请求会被代理截获，重新路由并修改参数后，发送给真实的自定义 LLM 服务。
4. **模型列表接管 (Model Discovery)**：许多客户端启动时会请求 `/v1/models` 获取可用模型。`llm-proxy` 会直接接管此请求，在服务启动时主动调用远端接口或读取本地缓存文件（如 `models/` 目录下）加载并合并所有支持的模型，最后将组合好的模型列表返回给客户端。

---

## 🚀 安装与部署

本项目支持 **源码启动** 和 **Docker Compose 部署** 两种方式。不管哪种方式，**都必须完成证书安装和 hosts 配置**。

### 准备工作 (必做)

1. **克隆项目到本地**：
   ```bash
   git clone https://github.com/charviki/llm-proxy.git
   cd llm-proxy
   ```
2. 将项目根目录下的 `config.example.yml` 复制为 `config.yml`。
3. 根据你的实际后端服务，修改 `config.yml` 中的路由和模型映射规则。
4. **(如果是 Docker 部署)**：将项目根目录下的 `docker-compose.example.yml` 复制为 `docker-compose.yml`。如果你在 `config.yml` 中配置了 `api_key_env`，需要在 `docker-compose.yml` 的 `environment` 节点中注入对应的真实 API Key（或者通过 `.env` 文件传递）。

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

#### 解决 443 端口与 VPN 冲突（可选但推荐）

默认情况下，容器可能会尝试绑定宿主机的全局 `0.0.0.0:443` 端口。这不仅可能与宿主机上运行的 Nginx 等其他 Web 服务产生端口冲突，**在开启某些 VPN 或代理软件（如 Clash、Surge、GlobalProtect 等）时，也极易发生路由或端口接管冲突，导致无法正常劫持请求**。
为了彻底解决这些冲突问题，本项目通过 `docker-compose.example.yml` 将端口映射到了独立的虚拟 IP（默认 `10.10.10.1`）。在启动前，你需要为宿主机配置该 **本地回环网卡别名 (Loopback Alias)**：

- **Linux / macOS**:
  ```bash
  # 临时生效（重启后丢失）
  sudo bash scripts/setup_network.sh
  
  # 永久生效（推荐，将注册为开机自启服务，确保 Docker 重启后能正常绑定 IP）
  sudo bash scripts/setup_network.sh --install
  ```
- **Windows**:
  使用 **管理员权限** 打开 PowerShell 并执行：
  ```powershell
  .\scripts\setup_network.ps1
  ```
*(注意：脚本内置了 IP 占用检测，如果 `10.10.10.1` 已被局域网内其他设备占用，请修改脚本和 `docker-compose.yml` 中的 IP 地址。)*

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

修改系统的 hosts 文件，将你需要劫持的域名（对应 `config.yml` 中的 `server.domains`）指向代理服务器的 IP 地址（本地即 `127.0.0.1`）。

编辑文件（macOS/Linux: `/etc/hosts`，Windows: `C:\Windows\System32\drivers\etc\hosts`），添加：

```text
# llm-proxy 劫持 (如果是源码启动或未配置虚拟 IP，请使用 127.0.0.1)
# 127.0.0.1 api.openai.com

# 如果使用了虚拟 IP 方案 (默认 10.10.10.1)，请将域名指向该 IP
10.10.10.1 api.openai.com
```

完成后，目标应用对 `api.openai.com` 的所有请求都会被 `llm-proxy` 截获，并按照你的 `config.yml` 规则路由到真实的自定义模型上！
