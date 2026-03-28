<div align="center">

# llm-proxy

**An MITM (Man-in-the-Middle) HTTPS proxy gateway designed specifically for Large Language Model (LLM) APIs.**

<p align="center">
  <a href="README.md">简体中文</a> | English
</p>

</div>

> [!IMPORTANT]
> **📝 Disclaimer**
>
> - This project is for personal learning purposes only. Stability is not guaranteed, and no technical support is provided.
> - Users must use this project in compliance with OpenAI's [Terms of Use](https://openai.com/policies/terms-of-use) and applicable **laws and regulations**. Do not use it for illegal purposes.
> - Please comply with local regulations regarding the provision of generative AI services.

## 💡 Why llm-proxy?

Many modern AI applications (like certain desktop clients, IDE plugins, etc.) hardcode official model API endpoints (e.g., `api.openai.com`) directly into their source code. They offer no settings for users to customize the Base URL or switch to private models.

`llm-proxy` solves this by generating a self-signed CA certificate and hijacking DNS/Hosts. It intercepts the HTTPS requests from these locked-down applications as an MITM proxy. After decrypting the requests, it seamlessly routes them to any LLM backend service you define (e.g., OpenRouter, internal private APIs). This effectively **breaks the application's restrictions, granting you the freedom to use your own custom models**.

## ✨ Key Features

- **HTTPS MITM Proxy & Auto Certificate Issuance**: Automatically generates a custom CA certificate and dynamically hijacks target domains (e.g., `api.openai.com`) based on configuration.
- **Strict OpenAI Protocol Compliance**: This project is built entirely on the standard OpenAI API protocol. Both the client requests and the backend APIs must adhere to the OpenAI format specification.
- **Unified API Access & Routing**: Seamlessly forwards standard requests to different LLM providers or custom backends that support the OpenAI protocol.
- **Transparent Authentication Replacement (API Key Injection)**: Clients can use any dummy API Key. The proxy will automatically read the real API Key from environment variables based on the routing rules and inject it before forwarding the request. This is incredibly useful when using private models in untrusted or highly restricted client environments.
- **Flexible Model Routing & Mapping**: Define robust request distribution rules via `config.yml`:
  - **Prefix Routing (Group Routing)**: When a client requests a model name with a specific prefix (e.g., `provider-a/gpt-4o`), the proxy automatically strips the prefix and routes the request to the corresponding provider's API endpoint. Ideal for managing aggregator API services.
  - **Exact Mapping (API Routing)**: Allows seamless translation of a specific model name requested by the client (e.g., `my-custom-model-v1`) into the actual model name required by the backend (e.g., `claude-3-5-sonnet`), and routes it to a designated private endpoint. Perfect for "disguising" or "renaming" models for the client.
- **Intelligent Reasoning Parsing**: Built-in parsers to adapt to the reasoning/thinking process output formats of different models (extracting specific `<think>` tags or separate `reasoning` fields), uniformly converting them into the standard OpenAI protocol format (e.g., `reasoning_content`) before returning to the client, ensuring correct rendering of the thought process on the client UI.
- **Stream Simulation**: For backend services that do not support streaming output, the proxy can automatically downgrade to sending non-streaming requests. It then takes the complete response and simulates a standard SSE streaming output via the `StreamSimulator`, perfectly maintaining compatibility with client applications that strictly require streaming input.
- **Unified Internal Event Stream Architecture**: Native SSE and non-streaming JSON are first normalized into a shared internal event stream, then consumed by either the streaming processor or the JSON assembler. This lets streaming cleanup, semantic coalescing, and JSON aggregation share the same core pipeline instead of maintaining two main branches.
- **SSE Semantic Coalescing**: Consecutive `content` deltas and `arguments` deltas for the same `tool_call` can be merged based on a time window and buffer length threshold, reducing overly fragmented SSE events. The feature is disabled by default and can be tuned incrementally per environment.
- **Runtime Traffic Recording & Replay**: Using FastAPI Middleware and a pluggable Transport Middleware chain, the proxy can record client request/response and backend request/response in real-time to JSON files in the `recordings/` directory. By sending an `X-Replay-Id` header, the proxy can seamlessly short-circuit backend requests and replay recorded mock responses, perfectly supporting isolated debugging and regression testing without consuming real tokens.

## ⚙️ How It Works

1. **Certificate Hijacking**: Install and trust the CA root certificate generated by `llm-proxy` on your host machine.
2. **DNS Spoofing**: Modify the system's `hosts` file to point target domains (like `api.openai.com`) to the proxy server (e.g., `127.0.0.1`).
3. **Seamless Forwarding**: Requests sent by client applications are intercepted, re-routed, and modified before being sent to the actual custom LLM service.
4. **Model Discovery Takeover**: Many clients request `/v1/models` on startup to get available models. `llm-proxy` completely takes over this request. During service startup, it actively calls remote backend interfaces or reads local cache files (e.g., in the `models/` directory) to load and merge all supported models, and then returns this combined list directly to the client without forwarding the request.
5. **Unified Response Handling**: For core endpoints such as `chat/completions`, the proxy first converts either native streaming responses or non-streaming JSON into a shared internal event stream through `BackendClient`, and then routes that stream to the SSE processor or the JSON assembler.

---

## 📚 Documentation & Guidelines

This project embraces an **incremental documentation principle** (`AGENTS.md`) tailored for both Developers and AI Agents. Please read the corresponding guidelines before modifying code:

- 🏠 **[Project Root Guide (AGENTS.md)](AGENTS.md)**: Overall architecture, design principles, and global development standards.
- ⚙️ **[Proxy Core Guide (proxy/AGENTS.md)](proxy/AGENTS.md)**: Detailed explanation of proxy logic, reasoning parsers, stream simulators, and performance standards.
- 🧪 **[Agent Testing Guide (tests/agent/README.md)](tests/agent/README.md)**: Instructions for using recording tools to capture real LLM traffic, how to replay mock data, and how to verify code changes via automated tests.

---

## 🚀 Installation & Deployment

You can run this project from **source code** or using **Docker Compose**. Regardless of the method, **you must complete the certificate installation and hosts configuration**.

### Preparation (Required)

> ⚠️ **System Requirements**
>
> Whether running locally or deploying via Docker, generating self-signed certificates requires the `openssl` command-line tool. Please ensure it is installed on your system:
> - **macOS**: `brew install openssl`
> - **Ubuntu/Debian**: `sudo apt update && sudo apt install openssl`
> - **Windows**: Recommended to install via [Scoop](https://scoop.sh/): `scoop install openssl`

1. **Clone the repository**:
   ```bash
   git clone https://github.com/charviki/llm-proxy.git
   cd llm-proxy
   ```
2. Copy `config.example.yml` to `config.yml` in the project root.
3. Modify the routing and model mapping rules in `config.yml` according to your actual backend services.
4. If you want to reduce overly fragmented SSE events, optionally configure `sse_coalescing.enabled / window_ms / max_buffer_length` in `config.yml`. It is disabled by default to preserve existing behavior.
5. **(For Docker Deployment)**: Copy `docker-compose.example.yml` to `docker-compose.yml`. If you have configured `api_key_env` in your `config.yml`, you must inject the corresponding real API Keys in the `environment` section of `docker-compose.yml` (or pass them via a `.env` file).

### Method 1: Running from Source (Local Development)

This project uses [uv](https://github.com/astral-sh/uv) for dependency management.

1. **Install dependencies**:

   ```bash
   uv sync
   ```

2. **Generate CA and Server Certificates**:
   _You must generate certificates before starting the service!_

   ```bash
   uv run python -m cli cert
   ```

   This will generate `llm-proxy.crt` (public key) and `llm-proxy.key` (private key) in the `ca/` directory.

3. **Run the service**:
   ```bash
   sudo uv run python main.py
   ```
   _Note: Administrator privileges (`sudo`) are usually required because the proxy needs to listen on the standard HTTPS port `443` for hijacking._

### Method 2: Docker Compose Deployment (Recommended)

Using Docker is cleaner, and the container will automatically generate the certificates for you.

#### Starting the Service

1. **Start the container**:
   ```bash
   docker compose up -d --build
   ```
2. **Extract the generated certificates**:
   After the container starts, the certificates are automatically generated and mounted to the `./ca` directory on your host. You need to access the `./ca/llm-proxy-ca.crt` file for the next step.

---

## 🔧 Client Configuration (Core Steps)

For the proxy to take effect, you **MUST** complete the following two configuration steps on the machine running the target application (e.g., your IDE):

### 1. Install and Trust the CA Certificate

Locate the `ca/llm-proxy-ca.crt` file (this is the root CA certificate) generated in the previous steps and install it into your system's Trusted Root Certification Authorities.

> **💡 Important Note for Windows + WSL Users**: If your IDE runs on Windows but connects to WSL (e.g., Ubuntu) for remote development, the underlying network requests from the IDE are very likely sent directly from the WSL environment. Therefore, you **MUST install and trust the certificate on BOTH the Windows host and the WSL subsystem**. Additionally, unless your WSL is configured to share DNS/Hosts with the Windows host, you must **modify the hosts file on both sides**!

- **macOS**:
  Double-click `llm-proxy-ca.crt`, find it in "Keychain Access", right-click -> Get Info -> expand "Trust" -> change "When using this certificate" to **Always Trust**.
- **Windows**:
  Double-click `llm-proxy-ca.crt` -> Install Certificate -> Select **Local Machine** -> Place all certificates in the following store -> Browse -> select **Trusted Root Certification Authorities**.
- **Linux**:
  Depending on your distribution, copy the certificate to `/usr/local/share/ca-certificates/` and run `sudo update-ca-certificates`.

### 2. Configure Hosts File to Hijack Domains

Modify your system's hosts file to point the domains you want to hijack (corresponding to `server.domains` in `config.yml`) to the proxy server's IP address.

- **Default Case**: point to `127.0.0.1`.
- **Using Port Forwarding Solution**: point to `127.0.0.2` (See the FAQ section at the end for details).

Edit the file (macOS/Linux: `/etc/hosts`, Windows: `C:\Windows\System32\drivers\etc\hosts`) and add:

```text
# llm-proxy hijacking
127.0.0.1 api.openai.com
# If using the port forwarding fallback solution, use:
# 127.0.0.2 api.openai.com
```

Once done, all requests made by the target application to `api.openai.com` will be intercepted by `llm-proxy` and routed to your custom models based on your `config.yml` rules!

---

## ❓ FAQ & Special Environment Configuration

### Resolving 443 Port & VPN Conflicts (Fallback Solution)

By default, the container in `docker-compose.yml` listens on the local `443` port (`443:443`). In most cases, this is sufficient. 

However, this may cause port conflicts with other web services like Nginx running on the host, or **it is highly prone to routing or port takeover conflicts when certain VPN or proxy software (like Clash, Surge, etc.) is active, which can prevent the proxy from hijacking requests normally**. At this point, you need to adopt the **Loopback + Port Forwarding** fallback solution.

The core idea of this solution is: Let the container listen to port `18443`, and configure an independent loopback IP (like `127.0.0.2` or `127.0.0.3`) in the system to forward the 443 port traffic sent to this IP to the local 18443.

**Step 1: Modify Port Mapping**
Change the `ports` in `docker-compose.yml` to listen on the local `18443` port:
```yaml
ports:
  - "18443:443"
```

**Step 2: Configure Port Forwarding and Hosts Based on Your System**

#### ▶ Linux / macOS Host

1. Run the provided network setup scripts to forward requests sent to `127.0.0.2:443` to the local `18443` port:
   ```bash
   # Temporary effect (lost after reboot)
   sudo bash scripts/setup_network.sh
   
   # Persistent effect (Recommended, registers as an auto-start service)
   sudo bash scripts/setup_network.sh --install
   ```
2. **Modify Hosts**: In the client's `/etc/hosts`, point the domains you need to hijack to `127.0.0.2`.

#### ▶ Windows Host (Without WSL Development)

1. Open PowerShell with **Administrator privileges** and execute the script to forward requests sent to `127.0.0.2:443` to the local `18443` port:
   ```powershell
   .\scripts\setup_network.ps1
   ```
2. **Modify Hosts**: In the Windows Hosts file, point the domains you need to hijack to `127.0.0.2`.

#### ▶ Windows + WSL Development Environment (e.g., VSCode WSL)

If you are developing in WSL, because the port has changed to 18443, WSL cannot directly access the proxy container on the host. You must separately configure the `127.0.0.3` loopback and forwarding for WSL:

1. **Enable Mirrored Networking Mode**
   Add or modify the `C:\Users\<YourUsername>\.wslconfig` file in Windows:
   ```ini
   [wsl2]
   networkingMode=mirrored
   ```
   *(After modification, please restart WSL: Execute `wsl --shutdown` in Windows PowerShell)*

2. **Disable Auto-Generation of Hosts in WSL**
   Enter WSL, edit `/etc/wsl.conf`:
   ```ini
   [network]
   generateHosts = false
   ```

3. **Configure WSL Hosts**
   Add hijacking records in WSL's `/etc/hosts`:
   ```text
   127.0.0.3 api.openai.com
   ```

4. **Configure WSL systemd Service for Port Forwarding**
   Execute the following commands in WSL to install `socat` and create a `systemd` service to forward `127.0.0.3:443` traffic to the host-visible `18443` port:
   ```bash
   sudo apt update && sudo apt install socat
   
   sudo tee /etc/systemd/system/llm-proxy.service << 'EOF'
   [Unit]
   Description=LLM API Proxy (127.0.0.3:443 -> 127.0.0.1:18443)
   After=network.target

   [Service]
   Type=simple
   ExecStartPre=-/sbin/ip addr add 127.0.0.3/8 dev lo
   ExecStart=/usr/bin/socat TCP-LISTEN:443,bind=127.0.0.3,reuseaddr,fork TCP:127.0.0.1:18443
   ExecStopPost=-/sbin/ip addr del 127.0.0.3/8 dev lo
   Restart=always
   RestartSec=3

   [Install]
   WantedBy=multi-user.target
   EOF

   sudo systemctl daemon-reload
   sudo systemctl enable --now llm-proxy.service
   ```

### Resolving 443 Port & VPN Conflicts (Fallback Solution)

By default, the container in `docker-compose.yml` listens on the local `443` port (`443:443`). In most cases, this is sufficient. 

However, this may cause port conflicts with other web services like Nginx running on the host, or **it is highly prone to routing or port takeover conflicts when certain VPN or proxy software (like Clash, Surge, etc.) is active, which can prevent the proxy from hijacking requests normally**. At this point, you need to adopt the **Loopback + Port Forwarding** fallback solution.

The core idea of this solution is: Let the container listen to port `18443`, and configure an independent loopback IP (like `127.0.0.2` or `127.0.0.3`) in the system to forward the 443 port traffic sent to this IP to the local 18443.

**Step 1: Modify Port Mapping**
Change the `ports` in `docker-compose.yml` to listen on the local `18443` port:
```yaml
ports:
  - "18443:443"
```

**Step 2: Configure Port Forwarding and Hosts Based on Your System**

#### ▶ Linux / macOS Host

1. Run the provided network setup scripts to forward requests sent to `127.0.0.2:443` to the local `18443` port:
   ```bash
   # Temporary effect (lost after reboot)
   sudo bash scripts/setup_network.sh
   
   # Persistent effect (Recommended, registers as an auto-start service)
   sudo bash scripts/setup_network.sh --install
   ```
2. **Modify Hosts**: In the client's `/etc/hosts`, point the domains you need to hijack to `127.0.0.2`.

#### ▶ Windows Host (Without WSL Development)

1. Open PowerShell with **Administrator privileges** and execute the script to forward requests sent to `127.0.0.2:443` to the local `18443` port:
   ```powershell
   .\scripts\setup_network.ps1
   ```
2. **Modify Hosts**: In the Windows Hosts file, point the domains you need to hijack to `127.0.0.2`.

#### ▶ Windows + WSL Development Environment (e.g., VSCode WSL)

If you are developing in WSL, because the port has changed to 18443, WSL cannot directly access the proxy container on the host. You must separately configure the `127.0.0.3` loopback and forwarding for WSL:

1. **Enable Mirrored Networking Mode**
   Add or modify the `C:\Users\<YourUsername>\.wslconfig` file in Windows:
   ```ini
   [wsl2]
   networkingMode=mirrored
   ```
   *(After modification, please restart WSL: Execute `wsl --shutdown` in Windows PowerShell)*

2. **Disable Auto-Generation of Hosts in WSL**
   Enter WSL, edit `/etc/wsl.conf`:
   ```ini
   [network]
   generateHosts = false
   ```

3. **Configure WSL Hosts**
   Add hijacking records in WSL's `/etc/hosts`:
   ```text
   127.0.0.3 api.openai.com
   ```

4. **Configure WSL systemd Service for Port Forwarding**
   Execute the following commands in WSL to install `socat` and create a `systemd` service to forward `127.0.0.3:443` traffic to the host-visible `18443` port:
   ```bash
   sudo apt update && sudo apt install socat
   
   sudo tee /etc/systemd/system/llm-proxy.service << 'EOF'
   [Unit]
   Description=LLM API Proxy (127.0.0.3:443 -> 127.0.0.1:18443)
   After=network.target

   [Service]
   Type=simple
   ExecStartPre=-/sbin/ip addr add 127.0.0.3/8 dev lo
   ExecStart=/usr/bin/socat TCP-LISTEN:443,bind=127.0.0.3,reuseaddr,fork TCP:127.0.0.1:18443
   ExecStopPost=-/sbin/ip addr del 127.0.0.3/8 dev lo
   Restart=always
   RestartSec=3

   [Install]
   WantedBy=multi-user.target
   EOF

   sudo systemctl daemon-reload
   sudo systemctl enable --now llm-proxy.service
   ```
