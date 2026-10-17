# Run the relay automatically when this (VM) user logs on.
#   powershell -ExecutionPolicy Bypass -File scripts\register_autostart.ps1
# The task runs in the interactive session, because the bot needs the visible desktop.
$root = Split-Path $PSScriptRoot -Parent
$action = New-ScheduledTaskAction -Execute "powershell.exe" `
    -Argument "-NoProfile -ExecutionPolicy Bypass -WindowStyle Minimized -File `"$root\start_relay.ps1`"" `
    -WorkingDirectory $root
# Give WeChat time to start and log in after the desktop appears.
$trigger = New-ScheduledTaskTrigger -AtLogOn -User $env:USERNAME
$trigger.Delay = "PT90S"
$settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries `
    -ExecutionTimeLimit ([TimeSpan]::Zero) -MultipleInstances IgnoreNew
Register-ScheduledTask -TaskName "WeChatRelay" -Action $action -Trigger $trigger -Settings $settings `
    -Description "WeChat secondary-account relay" -Force | Out-Null
Write-Host "Registered scheduled task 'WeChatRelay' (starts 90s after $env:USERNAME logs on)."
