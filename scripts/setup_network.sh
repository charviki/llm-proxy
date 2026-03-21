#!/bin/bash

# llm-proxy 网络配置脚本
# 此脚本用于为 llm-proxy Docker 容器设置本地回环网卡别名。
# 主要解决代理程序与宿主机上的 Nginx 等 Web 服务，或 VPN (如 Clash/Surge) 之间的端口/路由冲突。

TARGET_IP="10.10.10.1"
INSTALL_PERSISTENT=0

if [ "$1" = "--install" ]; then
    INSTALL_PERSISTENT=1
fi

# 检查目标 IP 是否已被占用
check_ip_occupation() {
    local ip=$1
    echo "正在检查 IP $ip 是否可用..."
    
    # 检查是否已经绑定到本地回环网卡
    local already_bound=0
    if command -v ip >/dev/null 2>&1; then
        if ip addr show lo 2>/dev/null | grep -q "inet ${ip}/"; then
            echo "IP $ip 已经绑定到本地回环网卡 (Linux)。"
            already_bound=1
        fi
    elif command -v ifconfig >/dev/null 2>&1; then
        if ifconfig lo0 2>/dev/null | grep -q "inet ${ip} "; then
            echo "IP $ip 已经绑定到本地回环网卡 (macOS)。"
            already_bound=1
        fi
    fi

    if [ "$already_bound" -eq 1 ]; then
        if [ "$INSTALL_PERSISTENT" -eq 0 ]; then
            echo "配置成功完成。IP: $TARGET_IP"
            exit 0
        else
            return 0 # 继续往下执行持久化安装
        fi
    fi

    # 使用 ping 检查 IP 是否被网络中的其他设备占用
    if ping -c 1 -W 1 "$ip" >/dev/null 2>&1 || ping -c 1 -t 1 "$ip" >/dev/null 2>&1; then
        echo "错误: IP $ip 已经被网络上的其他设备占用。"
        echo "请修改此脚本中的 TARGET_IP 变量，使用一个未被占用的 IP。"
        exit 1
    fi
    echo "IP $ip 可用。"
}

# 配置 macOS 环境
setup_macos() {
    if [ "$EUID" -ne 0 ]; then
        echo "请使用 root (sudo) 权限运行此脚本以配置网络接口。"
        exit 1
    fi

    if [ "$INSTALL_PERSISTENT" -eq 1 ]; then
        echo "正在安装 macOS 持久化 LaunchDaemon 服务..."
        PLIST_PATH="/Library/LaunchDaemons/com.llmproxy.network.plist"
        
        cat <<EOF > "$PLIST_PATH"
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>com.llmproxy.network</string>
    <key>ProgramArguments</key>
    <array>
        <string>/sbin/ifconfig</string>
        <string>lo0</string>
        <string>alias</string>
        <string>${TARGET_IP}/32</string>
    </array>
    <key>RunAtLoad</key>
    <true/>
</dict>
</plist>
EOF
        chmod 644 "$PLIST_PATH"
        chown root:wheel "$PLIST_PATH"
        launchctl load "$PLIST_PATH"
        echo "持久化 LaunchDaemon 服务已安装并加载。"
    else
        echo "正在应用临时回环网卡别名..."
        ifconfig lo0 alias "${TARGET_IP}/32"
        echo "别名已应用。注意：此配置将在重启后失效。使用 --install 参数可实现持久化。"
    fi
}

# 配置 Linux 环境
setup_linux() {
    if [ "$EUID" -ne 0 ]; then
        echo "请使用 root (sudo) 权限运行此脚本以配置网络接口。"
        exit 1
    fi

    if [ "$INSTALL_PERSISTENT" -eq 1 ]; then
        echo "正在安装 Linux 持久化 systemd 服务..."
        SERVICE_PATH="/etc/systemd/system/llm-proxy-network.service"
        
        cat <<EOF > "$SERVICE_PATH"
[Unit]
Description=LLM Proxy Loopback Alias
After=network.target
Before=docker.service

[Service]
Type=oneshot
ExecStart=/sbin/ip addr add ${TARGET_IP}/32 dev lo
RemainAfterExit=yes

[Install]
WantedBy=multi-user.target
EOF
        systemctl daemon-reload
        systemctl enable llm-proxy-network.service
        systemctl start llm-proxy-network.service
        echo "持久化 systemd 服务已安装并启动。"
    else
        echo "正在应用临时回环网卡别名..."
        ip addr add "${TARGET_IP}/32" dev lo
        echo "别名已应用。注意：此配置将在重启后失效。使用 --install 参数可实现持久化。"
    fi
}

check_ip_occupation "$TARGET_IP"

OS=$(uname -s)
case "$OS" in
    Darwin)
        setup_macos
        ;;
    Linux)
        setup_linux
        ;;
    *)
        echo "不支持的操作系统: $OS。请手动配置。"
        exit 1
        ;;
esac

echo "配置成功完成。IP: $TARGET_IP"
