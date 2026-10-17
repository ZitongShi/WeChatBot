# One-time setup inside the VM. Run in an elevated PowerShell:
#   powershell -ExecutionPolicy Bypass -File scripts\setup_vm.ps1
$ErrorActionPreference = "Stop"
$root = Split-Path $PSScriptRoot -Parent
Set-Location $root

Write-Host "== Chinese OCR language for Windows.Media.Ocr"
$ocr = Get-WindowsCapability -Online -Name "Language.OCR~~~zh-CN~0.0.1.0"
if ($ocr.State -ne "Installed") {
    Add-WindowsCapability -Online -Name "Language.OCR~~~zh-CN~0.0.1.0" | Out-Null
}

Write-Host "== Keep the desktop alive (no sleep, no screen off)"
powercfg /change standby-timeout-ac 0
powercfg /change monitor-timeout-ac 0
powercfg /change hibernate-timeout-ac 0

Write-Host "== Python virtualenv"
if (-not (Test-Path ".venv\Scripts\python.exe")) {
    $py = (Get-Command py -ErrorAction SilentlyContinue)
    if ($py) { py -3 -m venv .venv } else { python -m venv .venv }
}
& .\.venv\Scripts\python.exe -m pip install --upgrade pip
& .\.venv\Scripts\python.exe -m pip install -r requirements.txt

Write-Host "== Config"
if (-not (Test-Path "relay.json")) {
    Copy-Item relay.example.json relay.json
    Write-Host "   created relay.json (edit window_rect / forward_to if needed)"
}
New-Item -ItemType Directory -Force data\relay | Out-Null
if (-not (Test-Path "data\relay\soclaas.env")) {
    Write-Warning "data\relay\soclaas.env is missing: create it with SOCLAAS_API_KEY=... and SOCLAAS_BASE_URL=..."
}

Write-Host "== Unit tests"
$env:PYTHONPATH = Join-Path $root "src"
& .\.venv\Scripts\python.exe -m pytest -q tests

Write-Host "Done. Next: log in to WeChat, then run: .\start_relay.ps1 -Check"
