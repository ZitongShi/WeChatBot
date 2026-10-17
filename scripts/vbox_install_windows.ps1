# Unattended Windows install into the VirtualBox VM "WeChatRelay".
# Run it yourself so the VM user's password never leaves this window:
#   powershell -ExecutionPolicy Bypass -File scripts\vbox_install_windows.ps1
param(
    [string]$Vm = "WeChatRelay",
    [string]$Iso = "E:\VMs\downloads\Windows11_Client_x64_zh-cn_26300_9457.iso"
)
$ErrorActionPreference = "Stop"
$vbm = "C:\Program Files\Oracle\VirtualBox\VBoxManage.exe"

# Pick the Pro edition from the multi-edition ISO (falls back to the first image).
$detect = & $vbm unattended detect --iso=$Iso --machine-readable
$index = 1
foreach ($line in $detect) {
    # e.g. ImageIndex4="Windows 11 专业版 (10.0.26300.9457 / x64 / zh-CN)"
    if ($line -match '^ImageIndex(\d+)="Windows 1\d (专业版|Pro) \(') {
        $index = [int]$Matches[1]; break
    }
}
Write-Host "Using image index $index"

$user = Read-Host "虚拟机 Windows 用户名（例如 relay）"
$secure = Read-Host "虚拟机 Windows 密码" -AsSecureString
$plain = [Runtime.InteropServices.Marshal]::PtrToStringBSTR([Runtime.InteropServices.Marshal]::SecureStringToBSTR($secure))
$pwFile = Join-Path $env:TEMP ("vbox-pw-" + [guid]::NewGuid() + ".txt")
try {
    [IO.File]::WriteAllText($pwFile, $plain)
    & $vbm unattended install $Vm `
        --iso=$Iso `
        --image-index=$index `
        --user=$user `
        --password-file=$pwFile `
        --full-user-name=$user `
        --locale=zh_CN --country=CN --time-zone=Asia/Shanghai `
        --hostname=wechatrelay.local `
        --install-additions `
        --start-vm=gui
} finally {
    Remove-Item $pwFile -Force -ErrorAction SilentlyContinue
    $plain = $null
}
Write-Host "安装已开始：VirtualBox 窗口会自动重启几次，大约 20-40 分钟后进入桌面。"
