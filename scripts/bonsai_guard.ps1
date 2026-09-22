<#
.SYNOPSIS
Supervision step for the dedicated local bonsai-27b server: check, restart if down, log.

.DESCRIPTION
Designed to be run repeatedly (scheduled task every few minutes, or by hand). It is
the smallest thing that turns "the server was started from a terminal and died with
it" into "the server comes back".

Decision table, driven by the exit code of scripts\bonsai_health.ps1:

  0  healthy   -> no action; the state file is updated and nothing is logged
  1  down      -> start the server through scripts\start-local-bonsai.ps1, re-check
  2  wrong store -> DO NOT start anything. A process already owns the port and reads
                    another OLLAMA_MODELS; starting a second one cannot bind the port
                    and would only hide the real problem (a bad environment inherited
                    from whatever launched Ollama). Logged as an action item.

This script NEVER kills or restarts a foreign process. Killing the process that owns
the port is a human decision, so it is reported, not performed.

.PARAMETER Port / Model / ModelsDir / ContextLength / KeepAlive
Passed through to scripts\start-local-bonsai.ps1. Defaults match the verified
operating point for bonsai-27b on this workstation (RTX 2070, 8 GB VRAM): 8192-token
context, model pinned in VRAM for 30 minutes.

.PARAMETER LogDir
Where the guard writes. Default <repo>\logs. Two files: bonsai_guard.log (state
changes and failures only, so it does not grow on heartbeats) and
bonsai_guard.state.json (last known status for dashboards and for a human).

.EXAMPLE
powershell -ExecutionPolicy Bypass -File scripts\bonsai_guard.ps1

.EXAMPLE
powershell -ExecutionPolicy Bypass -File scripts\bonsai_guard.ps1 -Port 8088 -ContextLength 8192
#>
[CmdletBinding()]
param(
    [int]$Port = 8088,
    [string]$Model = 'bonsai-27b',
    [string]$ModelsDir = "$env:USERPROFILE\.ollama\models",
    [int]$ContextLength = 8192,
    [string]$KeepAlive = '30m',
    [string]$LogDir = '',
    [switch]$LogHeartbeat
)

$ErrorActionPreference = 'Stop'
$RepoRoot = Split-Path -Parent (Split-Path -Parent $PSCommandPath)
if (-not $LogDir) { $LogDir = Join-Path $RepoRoot 'logs' }
New-Item -ItemType Directory -Path $LogDir -Force | Out-Null

$LogFile = Join-Path $LogDir 'bonsai_guard.log'
$StateFile = Join-Path $LogDir 'bonsai_guard.state.json'
$HealthScript = Join-Path $RepoRoot 'scripts\bonsai_health.ps1'
$StartScript = Join-Path $RepoRoot 'scripts\start-local-bonsai.ps1'

function Write-GuardLog {
    param([string]$Level, [string]$Message)
    $line = "{0} {1,-7} {2}" -f (Get-Date).ToString('s'), $Level, $Message
    Add-Content -Path $LogFile -Value $line -Encoding UTF8
    Write-Host $line
}

function Get-Health {
    param([int]$Attempts = 1, [int]$SleepSec = 5)
    for ($i = 1; $i -le $Attempts; $i++) {
        # A child process is required: bonsai_health.ps1 signals with `exit`.
        $out = & powershell.exe -NoProfile -ExecutionPolicy Bypass -File $HealthScript `
                -Port $Port -Model $Model -ModelsDir $ModelsDir 2>&1
        $code = $LASTEXITCODE
        if ($code -eq 0 -or $i -eq $Attempts) {
            return [pscustomobject]@{ Code = $code; Text = ($out -join ' | ') }
        }
        Start-Sleep -Seconds $SleepSec
    }
}

function Save-State {
    param([int]$Code, [string]$Detail, [string]$Action)
    [ordered]@{
        at      = (Get-Date).ToString('s')
        port    = $Port
        model   = $Model
        exit_code = $Code
        action  = $Action
        detail  = $Detail
    } | ConvertTo-Json -Depth 3 | Set-Content -Path $StateFile -Encoding UTF8
}

# The desktop app installs ollama.exe into the user profile; a scheduled task may run
# with a PATH that does not know that directory yet.
if (-not (Get-Command ollama -ErrorAction SilentlyContinue)) {
    $candidate = Join-Path $env:LOCALAPPDATA 'Programs\Ollama'
    if (Test-Path (Join-Path $candidate 'ollama.exe')) {
        $env:Path = "$candidate;$env:Path"
        Write-GuardLog 'INFO' "added $candidate to PATH for this run"
    }
}

$health = Get-Health
switch ($health.Code) {
    0 {
        if ($LogHeartbeat) { Write-GuardLog 'INFO' "healthy: $($health.Text)" }
        Save-State 0 $health.Text 'none'
        exit 0
    }
    2 {
        Write-GuardLog 'ERROR' "port $Port is answered by a server that does not list '$Model'"
        Write-GuardLog 'ERROR' "no start attempted; fix the OLLAMA_MODELS of the process owning $Port"
        Write-GuardLog 'ERROR' "probe: $($health.Text)"
        Save-State 2 $health.Text 'refused-store-mismatch'
        exit 2
    }
    1 {
        Write-GuardLog 'WARN' "server on port $Port is down, starting it"
        try {
            & $StartScript -Port $Port -Model $Model -ModelsDir $ModelsDir `
                -ContextLength $ContextLength -KeepAlive $KeepAlive
        } catch {
            Write-GuardLog 'ERROR' "start-local-bonsai.ps1 failed: $($_.Exception.Message)"
            Save-State 1 $_.Exception.Message 'start-failed'
            exit 1
        }

        $after = Get-Health -Attempts 6 -SleepSec 5
        if ($after.Code -eq 0) {
            Write-GuardLog 'INFO' "recovered: $($after.Text)"
            Save-State 0 $after.Text 'started-and-healthy'
            exit 0
        }
        Write-GuardLog 'ERROR' "still unhealthy after start attempt: $($after.Text)"
        Save-State $after.Code $after.Text 'start-but-still-unhealthy'
        exit $after.Code
    }
    default {
        Write-GuardLog 'ERROR' "unknown probe result: $($health.Text)"
        Save-State $health.Code $health.Text 'unknown'
        exit $health.Code
    }
}
