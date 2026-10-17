# Provision the relay inside the VM. Run in an elevated PowerShell inside the VM:
#   powershell -ExecutionPolicy Bypass -File \\VBOXSVR\relay\bootstrap_guest.ps1
# Expects soclaas.env next to this script (the host shares E:\VMs\share read-only).
param(
    [string]$Target = "C:\WeChatBot",
    [string]$Branch = "claude/wechat-relay"
)
$ErrorActionPreference = "Stop"
$ProgressPreference = "SilentlyContinue"
$share = $PSScriptRoot
$tmp = Join-Path $env:TEMP "relay-bootstrap"
New-Item -ItemType Directory -Force $tmp | Out-Null

Write-Host "== 1/6 Screen 1920x1080, no screen saver, no lock on wake"
Add-Type @"
using System; using System.Runtime.InteropServices;
public static class Res {
  [StructLayout(LayoutKind.Sequential, CharSet=CharSet.Unicode)]
  public struct DEVMODE {
    [MarshalAs(UnmanagedType.ByValTStr, SizeConst=32)] public string dmDeviceName;
    public short dmSpecVersion, dmDriverVersion, dmSize, dmDriverExtra; public int dmFields;
    public int dmPositionX, dmPositionY, dmDisplayOrientation, dmDisplayFixedOutput;
    public short dmColor, dmDuplex, dmYResolution, dmTTOption, dmCollate;
    [MarshalAs(UnmanagedType.ByValTStr, SizeConst=32)] public string dmFormName;
    public short dmLogPixels; public int dmBitsPerPel, dmPelsWidth, dmPelsHeight, dmDisplayFlags, dmDisplayFrequency;
    public int dmICMMethod, dmICMIntent, dmMediaType, dmDitherType, dmReserved1, dmReserved2, dmPanningWidth, dmPanningHeight;
  }
  [DllImport("user32.dll", CharSet=CharSet.Unicode)] public static extern bool EnumDisplaySettings(string d, int m, ref DEVMODE dm);
  [DllImport("user32.dll", CharSet=CharSet.Unicode)] public static extern int ChangeDisplaySettings(ref DEVMODE dm, int f);
  public static int Set(int w, int h) {
    DEVMODE dm = new DEVMODE(); dm.dmSize = (short)Marshal.SizeOf(typeof(DEVMODE));
    EnumDisplaySettings(null, -1, ref dm); dm.dmPelsWidth = w; dm.dmPelsHeight = h; dm.dmFields = 0x80000 | 0x100000;
    return ChangeDisplaySettings(ref dm, 1);
  }
}
"@
$rc = [Res]::Set(1920, 1080)
Write-Host "   resolution change result: $rc (0 = ok)"
Set-ItemProperty "HKCU:\Control Panel\Desktop" -Name ScreenSaveActive -Value "0"
powercfg /SETACVALUEINDEX SCHEME_CURRENT SUB_NONE CONSOLELOCK 0
powercfg /SETACTIVE SCHEME_CURRENT

Write-Host "== 2/6 Python 3.12"
if (-not (Test-Path "C:\Program Files\Python312\python.exe")) {
    $py = Join-Path $tmp "python-3.12.10-amd64.exe"
    Invoke-WebRequest "https://www.python.org/ftp/python/3.12.10/python-3.12.10-amd64.exe" -OutFile $py
    Start-Process $py -ArgumentList "/quiet", "InstallAllUsers=1", "PrependPath=1", "Include_test=0" -Wait
}
$env:Path = [Environment]::GetEnvironmentVariable("Path", "Machine") + ";" + [Environment]::GetEnvironmentVariable("Path", "User")

Write-Host "== 3/6 Relay code"
$source = Join-Path $share "WeChatBot"
if (-not (Test-Path $source)) {
    # No copy on the share: fall back to the GitHub branch.
    $zip = Join-Path $tmp "relay.zip"
    Invoke-WebRequest "https://github.com/ZitongShi/WeChatBot/archive/refs/heads/$Branch.zip" -OutFile $zip
    $extract = Join-Path $tmp "src"
    Remove-Item $extract -Recurse -Force -ErrorAction SilentlyContinue
    Expand-Archive $zip -DestinationPath $extract
    $source = (Get-ChildItem $extract -Directory | Select-Object -First 1).FullName
}
New-Item -ItemType Directory -Force $Target | Out-Null
# Keep local state (venv, config, logs) when re-running to update the code.
robocopy $source $Target /E /XD .venv data logs .git __pycache__ /XF relay.json /NFL /NDL /NJH /NJS | Out-Null

Write-Host "== 4/6 API key"
New-Item -ItemType Directory -Force (Join-Path $Target "data\relay") | Out-Null
Copy-Item (Join-Path $share "soclaas.env") (Join-Path $Target "data\relay\soclaas.env") -Force

Write-Host "== 5/6 Dependencies, OCR language, power settings, tests"
& powershell -ExecutionPolicy Bypass -File (Join-Path $Target "scripts\setup_vm.ps1")

Write-Host "== 6/6 WeChat 4.1.15 installer"
if (-not (Test-Path "C:\Program Files\Tencent\Weixin\Weixin.exe")) {
    $wx = Join-Path $tmp "WeChatWin_4.1.15.exe"
    Invoke-WebRequest "https://dldir1v6.qq.com/weixin/Universal/Windows/WeChatWin_4.1.15.exe" -OutFile $wx
    Start-Process $wx
    Write-Host "   WeChat installer started: finish it, then log in to the secondary account with your phone."
}
Write-Host "Done. After WeChat is logged in: cd $Target; .\start_relay.ps1 -Check"
