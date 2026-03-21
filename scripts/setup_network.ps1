# llm-proxy Windows 网络配置脚本
# 此脚本用于为 llm-proxy Docker 容器设置本地回环网卡别名。
# 主要解决代理程序与宿主机上的其他 Web 服务，或 VPN (如 Clash/Surge 等) 之间的冲突。

$TARGET_IP = "10.10.10.1"

# 检查是否拥有管理员权限
$currentPrincipal = New-Object Security.Principal.WindowsPrincipal([Security.Principal.WindowsIdentity]::GetCurrent())
if (-not $currentPrincipal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
    Write-Warning "配置网络接口需要管理员权限。"
    Write-Host "请以管理员身份重新运行 PowerShell，然后再次执行此脚本。"
    Exit
}

# 检查 IP 是否被占用
Write-Host "正在检查 IP $TARGET_IP 是否可用..."

# 检查是否已经绑定到本地 Loopback 接口
$existingIP = Get-NetIPAddress -IPAddress $TARGET_IP -InterfaceAlias "Loopback Pseudo-Interface 1" -ErrorAction SilentlyContinue
if ($existingIP) {
    Write-Host "IP $TARGET_IP 已经绑定到本地回环网卡。配置完成。"
    Exit
}

# 使用 Ping 检查 IP 是否被网络中的其他设备占用
$pingResult = Test-Connection -ComputerName $TARGET_IP -Count 1 -Quiet
if ($pingResult) {
    Write-Error "错误: IP $TARGET_IP 已经被网络上的其他设备占用。"
    Write-Host "请修改此脚本中的 TARGET_IP 变量，使用一个未被占用的 IP。"
    Exit
}
Write-Host "IP $TARGET_IP 可用。"

# 应用别名到 Loopback 接口
Write-Host "正在应用回环网卡别名到 'Loopback Pseudo-Interface 1'..."
try {
    New-NetIPAddress -InterfaceAlias "Loopback Pseudo-Interface 1" -IPAddress $TARGET_IP -PrefixLength 32 -AddressFamily IPv4 -ErrorAction Stop | Out-Null
    Write-Host "配置成功完成。IP: $TARGET_IP (在 Windows 上配置默认持久化生效)。"
} catch {
    Write-Error "添加 IP 地址失败。请确保 'Loopback Pseudo-Interface 1' 接口存在。"
    Write-Error $_.Exception.Message
}
