<#
.SYNOPSIS
Installs the Windows scheduled task that keeps the local bonsai-27b server alive.

.DESCRIPTION
The dedicated server on port 8088 used to be started by hand from a terminal, which
means it died with that terminal and did not come back after a reboot. This script
registers one task that runs scripts\bonsai_guard.ps1:

  - at logon of the current user, then repeatedly every -IntervalMinutes;
  - single instance (IgnoreNew), no execution time limit, restart on failure;
  - runs as the current user at limited privilege - no elevation, no service.

The guard only starts a server when the port is not answering. If some other process
owns the port and reads a different OLLAMA_MODELS, the guard refuses to act and logs
it, because starting a second server cannot bind the port and would hide the real
misconfiguration. Nothing here kills a foreign process.

VRAM note: RTX 2070 has 8192 MiB and bonsai-27b occupies roughly 4.1 GiB at 8192-token
context. Only one server may hold the model; the desktop app on 11434 is for browsing
the store, not for a second concurrent load.

.PARAMETER IntervalMinutes
How often the guard re-checks. Default 5.

.PARAMETER TaskName
Task name in the root folder. Default WORED-Bonsai-8088.

.PARAMETER Uninstall
Remove the task. Nothing else is touched.

.PARAMETER RunNow
Start the task immediately after registering. Safe: the guard is a no-op while the
server is healthy.

.EXAMPLE
powershell -ExecutionPolicy Bypass -File scripts\install_bonsai_supervision.ps1 -RunNow

.EXAMPLE
powershell -ExecutionPolicy Bypass -File scripts\install_bonsai_supervision.ps1 -Uninstall
#>
[CmdletBinding()]
param(
    [int]$Port = 8088,
    [int]$IntervalMinutes = 5,
    [string]$TaskName = 'WORED-Bonsai-8088',
    [switch]$Uninstall,
    [switch]$RunNow
)

$ErrorActionPreference = 'Stop'
$RepoRoot = Split-Path -Parent (Split-Path -Parent $PSCommandPath)
$Guard = Join-Path $RepoRoot 'scripts\bonsai_guard.ps1'

if (-not (Test-Path $Guard)) { throw "guard script not found: $Guard" }

if ($Uninstall) {
    $existing = Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
    if ($existing) {
        Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false
        Write-Host "task '$TaskName' removed" -ForegroundColor Green
    } else {
        Write-Host "task '$TaskName' is not registered" -ForegroundColor Yellow
    }
    exit 0
}

$action = New-ScheduledTaskAction -Execute 'powershell.exe' -WorkingDirectory $RepoRoot `
    -Argument "-NoProfile -ExecutionPolicy Bypass -File `"$Guard`" -Port $Port"

$trigger = New-ScheduledTaskTrigger -AtLogOn -User "$env:USERDOMAIN\$env:USERNAME"
# A logon trigger alone fires once per session; the periodic re-check is a second
# trigger. Repetition on -Once is the only form Windows PowerShell 5.1 accepts
# without hand-building a MSFT_TaskRepetitionPattern CIM object, and a repetition
# interval without a duration is silently ignored by the task engine.
$every = New-ScheduledTaskTrigger -Once -At (Get-Date).AddMinutes(1) `
    -RepetitionInterval (New-TimeSpan -Minutes $IntervalMinutes) `
    -RepetitionDuration (New-TimeSpan -Days 9999)
$triggers = @($trigger, $every)

$settings = New-ScheduledTaskSettingsSet `
    -MultipleInstances IgnoreNew `
    -ExecutionTimeLimit (New-TimeSpan -Seconds 0) `
    -RestartCount 3 `
    -RestartInterval (New-TimeSpan -Minutes 1) `
    -StartWhenAvailable `
    -AllowStartIfOnBatteries `
    -DontStopIfGoingOnBatteries

$principal = New-ScheduledTaskPrincipal -UserId "$env:USERDOMAIN\$env:USERNAME" `
    -LogonType Interactive -RunLevel Limited

$description = "Keeps the dedicated local Ollama server (bonsai-27b on 127.0.0.1:$Port) alive" +
    " for WORED. Guard: $Guard"

Register-ScheduledTask -TaskName $TaskName -TaskPath '\' `
    -Action $action -Trigger $triggers -Settings $settings -Principal $principal `
    -Description $description -Force | Out-Null

$task = Get-ScheduledTask -TaskName $TaskName
$info = $task | Get-ScheduledTaskInfo

if ($RunNow) {
    Start-ScheduledTask -TaskName $TaskName
    Start-Sleep -Seconds 3
    $info = Get-ScheduledTask -TaskName $TaskName | Get-ScheduledTaskInfo
}

Write-Host "task      : $TaskName ($($task.State))" -ForegroundColor Green
Write-Host "run       : at logon + every $IntervalMinutes min, single instance" 
Write-Host "action    : powershell -File $Guard -Port $Port"
Write-Host "last run  : $($info.LastRunTime)  result: $($info.LastTaskResult)"
if ($task.State -eq 'Ready') {
    Write-Host "verify    : powershell -File scripts\bonsai_health.ps1 -AsJson" -ForegroundColor Cyan
}
