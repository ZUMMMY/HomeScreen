# Creates two Task Scheduler tasks for the current user:
#   "Madison Desktop Refresh"   runs fetch.py when you sign in and every 15 minutes
#   "Madison Desktop Race Mode" runs live.py; fetch.py books it for the minute each F1 session
#                               goes live (no time limit, one copy at a time)
# Missed runs (PC asleep or off) happen as soon as it's back.
#
# Run from the project folder:  powershell -ExecutionPolicy Bypass -File setup\register-tasks.ps1
# Remove them again with:       powershell -ExecutionPolicy Bypass -File setup\register-tasks.ps1 -Remove
# Keep -Minutes in step with refresh_minutes in config.toml.

param([switch]$Remove, [int]$Minutes = 15)

$ErrorActionPreference = "Stop"
$root = Split-Path $PSScriptRoot -Parent
$pythonw = Join-Path $root ".venv\Scripts\pythonw.exe"
$names = "Madison Desktop Refresh", "Madison Desktop Race Mode"

if ($Remove) {
    foreach ($n in $names) { Unregister-ScheduledTask -TaskName $n -Confirm:$false -ErrorAction SilentlyContinue }
    Write-Host "Removed the Madison Desktop tasks."
    exit 0
}

if (-not (Test-Path $pythonw)) {
    Write-Host "Run setup\install.ps1 first (no .venv found)."
    exit 1
}

$user = "$env:USERDOMAIN\$env:USERNAME"
$principal = New-ScheduledTaskPrincipal -UserId $user -LogonType Interactive -RunLevel Limited
$every = New-TimeSpan -Minutes $Minutes

# Refresh: when you sign in, then every $Minutes minutes for as long as the PC is on.
$refreshAction = New-ScheduledTaskAction -Execute $pythonw -Argument "fetch.py" -WorkingDirectory $root
$atLogon = New-ScheduledTaskTrigger -AtLogOn -User $user
$atLogon.Delay = "PT20S"  # let the desktop and network come up first
$repeat = New-ScheduledTaskTrigger -Once -At (Get-Date).AddMinutes(1) -RepetitionInterval $every
$refreshSettings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries `
    -StartWhenAvailable -ExecutionTimeLimit (New-TimeSpan -Minutes 5) -MultipleInstances IgnoreNew
Register-ScheduledTask -TaskName $names[0] -Action $refreshAction -Trigger $atLogon, $repeat `
    -Settings $refreshSettings -Principal $principal -Force | Out-Null

# Race mode: fetch.py gives it a one-time trigger at each session's go-live time, or starts it
# directly when a session is already on.
$raceAction = New-ScheduledTaskAction -Execute $pythonw -Argument "live.py" -WorkingDirectory $root
$raceSettings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries `
    -StartWhenAvailable -ExecutionTimeLimit (New-TimeSpan -Hours 7) -MultipleInstances IgnoreNew
Register-ScheduledTask -TaskName $names[1] -Action $raceAction -Settings $raceSettings `
    -Principal $principal -Force | Out-Null

Write-Host "Registered: $($names -join ', ') (refresh every $Minutes minutes)"
Start-ScheduledTask -TaskName $names[0]
