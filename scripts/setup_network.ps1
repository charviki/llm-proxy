﻿# llm-proxy Windows 网络配置脚本
# 此脚本用于为 llm-proxy 设置 127.0.0.2 的端口转发。
# 主要解决代理程序与宿主机上的其他 Web 服务，或 VPN (如 Clash/Surge 等) 之间的 443 端口冲突。

$TARGET_IP = "127.0.0.2"
$TARGET_PORT = "443"
$FORWARD_PORT = "18443"

# 检查是否拥有管理员权限
$currentPrincipal = New-Object Security.Principal.WindowsPrincipal([Security.Principal.WindowsIdentity]::GetCurrent())
if (-not $currentPrincipal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
    Write-Warning "配置网络转发规则需要管理员权限。"
    Write-Host "请以管理员身份重新运行 PowerShell，然后再次执行此脚本。"
    Exit
}

Write-Host "正在为 Windows 配置端口转发..."

# 使用 netsh 配置端口转发
try {
    # 先尝试删除旧规则（如果有）
    netsh interface portproxy delete v4tov4 listenport=$TARGET_PORT listenaddress=$TARGET_IP 2>$null
    
    # 添加新规则
    netsh interface portproxy add v4tov4 listenport=$TARGET_PORT listenaddress=$TARGET_IP connectport=$FORWARD_PORT connectaddress="127.0.0.1"
    
    Write-Host "配置成功完成。请求 ${TARGET_IP}:${TARGET_PORT} 将被转发到 127.0.0.1:${FORWARD_PORT}。"
    Write-Host "注意：Windows 上的 portproxy 配置默认是持久化的。"
}
catch {
    Write-Error "配置端口转发失败。"
    Write-Error $_.Exception.Message
}