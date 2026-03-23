#!/bin/bash

# llm-proxy 网络配置脚本
# 此脚本用于为 llm-proxy 设置 127.0.0.2 回环别名与端口转发。
# 主要解决代理程序与宿主机上的其他 Web 服务或 VPN 的 443 端口冲突。

TARGET_IP="127.0.0.2"
TARGET_PORT="443"
FORWARD_PORT="18443"
INSTALL_PERSISTENT=0

if [ "$1" = "--install" ]; then
    INSTALL_PERSISTENT=1
fi

if [ "$EUID" -ne 0 ]; then
    echo "请使用 root (sudo) 权限运行此脚本以配置网络与防火墙规则。"
    exit 1
fi

# 配置 macOS 环境
setup_macos() {
    echo "正在为 macOS 配置 $TARGET_IP 端口转发..."
    
    # 添加别名
    ifconfig lo0 alias "${TARGET_IP}/8" up
    
    # 启用 pf
    pfctl -E >/dev/null 2>&1
    
    # 写入 pf 转发规则
    echo "rdr pass on lo0 inet proto tcp from any to ${TARGET_IP} port ${TARGET_PORT} -> 127.0.0.1 port ${FORWARD_PORT}" | pfctl -a "com.apple/llm-proxy" -f -
    
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
        <string>/bin/sh</string>
        <string>-c</string>
        <string>/sbin/ifconfig lo0 alias ${TARGET_IP}/8 up; /sbin/pfctl -E; echo "rdr pass on lo0 inet proto tcp from any to ${TARGET_IP} port ${TARGET_PORT} -> 127.0.0.1 port ${FORWARD_PORT}" | /sbin/pfctl -a "com.apple/llm-proxy" -f -</string>
    </array>
    <key>RunAtLoad</key>
    <true/>
</dict>
</plist>
EOF
        chmod 644 "$PLIST_PATH"
        chown root:wheel "$PLIST_PATH"
        launchctl load -w "$PLIST_PATH"
        echo "持久化 LaunchDaemon 服务已安装并加载。"
    else
        echo "别名和 pf 规则已应用。注意：此配置将在重启后失效。使用 --install 参数可实现持久化。"
    fi
}

# 配置 Linux 环境
setup_linux() {
    echo "正在为 Linux 配置 $TARGET_IP 端口转发..."
    
    # 检查 iptables 是否存在
    if ! command -v iptables >/dev/null 2>&1; then
        echo "错误: 未找到 iptables 命令，请先安装 iptables。"
        exit 1
    fi
    
    # 清理可能存在的旧规则
    iptables -t nat -D OUTPUT -d ${TARGET_IP} -p tcp --dport ${TARGET_PORT} -j DNAT --to-destination 127.0.0.1:${FORWARD_PORT} 2>/dev/null
    
    # 添加新规则
    iptables -t nat -A OUTPUT -d ${TARGET_IP} -p tcp --dport ${TARGET_PORT} -j DNAT --to-destination 127.0.0.1:${FORWARD_PORT}
    
    # 确保本地路由支持
    sysctl -w net.ipv4.conf.all.route_localnet=1 >/dev/null 2>&1
    
    if [ "$INSTALL_PERSISTENT" -eq 1 ]; then
        echo "正在安装 Linux 持久化 systemd 服务..."
        SERVICE_PATH="/etc/systemd/system/llm-proxy-network.service"
        
        cat <<EOF > "$SERVICE_PATH"
[Unit]
Description=LLM Proxy Port Forwarding
After=network.target

[Service]
Type=oneshot
ExecStartPre=-/sbin/sysctl -w net.ipv4.conf.all.route_localnet=1
ExecStartPre=-/sbin/iptables -t nat -D OUTPUT -d ${TARGET_IP} -p tcp --dport ${TARGET_PORT} -j DNAT --to-destination 127.0.0.1:${FORWARD_PORT}
ExecStart=/sbin/iptables -t nat -A OUTPUT -d ${TARGET_IP} -p tcp --dport ${TARGET_PORT} -j DNAT --to-destination 127.0.0.1:${FORWARD_PORT}
RemainAfterExit=yes

[Install]
WantedBy=multi-user.target
EOF
        systemctl daemon-reload
        systemctl enable llm-proxy-network.service
        systemctl start llm-proxy-network.service
        echo "持久化 systemd 服务已安装并启动。"
    else
        echo "iptables 规则已应用。注意：此配置将在重启后失效。使用 --install 参数可实现持久化。"
    fi
}

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

echo "配置成功完成。请求 ${TARGET_IP}:${TARGET_PORT} 将被转发到 127.0.0.1:${FORWARD_PORT}。"
