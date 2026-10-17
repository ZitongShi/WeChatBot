# Start the WeChat relay (secondary account -> main account).
#   .\start_relay.ps1          run, restarting automatically if it crashes
#   .\start_relay.ps1 -Check   only save an annotated screenshot of the detected layout
param([switch]$Check)

$root = $PSScriptRoot
$env:PYTHONPATH = Join-Path $root "src"
$env:PYTHONIOENCODING = "utf-8"
$env:PYTHONUNBUFFERED = "1"
Set-Location $root
$python = Join-Path $root ".venv\Scripts\python.exe"

if ($Check) {
    & $python -m wechat_relay layout
    exit $LASTEXITCODE
}

New-Item -ItemType Directory -Force (Join-Path $root "logs") | Out-Null
$console = Join-Path $root "logs\relay_console.log"
while ($true) {
    "==== start $(Get-Date -Format 'yyyy-MM-dd HH:mm:ss')" | Out-File -Append -Encoding utf8 $console
    & $python -m wechat_relay run 2>&1 | Tee-Object -Append -FilePath $console
    "==== exited $LASTEXITCODE $(Get-Date -Format 'yyyy-MM-dd HH:mm:ss'), restarting in 15s" | Out-File -Append -Encoding utf8 $console
    Start-Sleep -Seconds 15
}
