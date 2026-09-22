<#
.SYNOPSIS
Health check for the dedicated local Ollama server that must always serve bonsai-27b.

.DESCRIPTION
Read-only probe. It answers one question: is the API on -Port alive AND does it list
the expected model. Exit code is the contract for scripts\bonsai_guard.ps1 and for
the scheduled task, so keep it stable:

  0  healthy            - server answers and the model is visible
  1  server down        - nothing answers on the port (or the probe itself failed)
  2  model missing      - server answers but does not list the model (wrong store)

Exit code 2 is the interesting one on this workstation: two `ollama serve` processes
can coexist, and the one started from a shell that carried OLLAMA_MODELS=<somewhere
else> answers /api/version happily while listing zero models. That is a store
mismatch, not a dead server, and restarting the server does not fix it.

.PARAMETER Port
Port of the dedicated server. Default 8088, chosen so it cannot collide with the
WORED webui host port 8080.

.PARAMETER Model
Model name that must be visible. Default bonsai-27b.

.PARAMETER ModelsDir
Store the server is supposed to read. Used only to explain a mismatch.

.PARAMETER CheckAppPort
Also probe the desktop app's server (default 11434) and warn when it sees no models
while ModelsDir does. Warning only - never a failure, the app is not part of the
WORED runtime contract.

.EXAMPLE
powershell -ExecutionPolicy Bypass -File scripts\bonsai_health.ps1

.EXAMPLE
powershell -ExecutionPolicy Bypass -File scripts\bonsai_health.ps1 -AsJson
#>
[CmdletBinding()]
param(
    [int]$Port = 8088,
    [string]$Model = 'bonsai-27b',
    [string]$ModelsDir = "$env:USERPROFILE\.ollama\models",
    [int]$CheckAppPort = 11434,
    [int]$TimeoutSec = 10,
    [switch]$AsJson
)

$ErrorActionPreference = 'Stop'
$BaseUrl = "http://127.0.0.1:$Port"
$lines = [ordered]@{
    checked_at   = (Get-Date).ToString('s')
    server       = $BaseUrl
    up           = $false
    version      = $null
    model        = $Model
    models       = @()
    store        = $ModelsDir
    store_models = @()
    notes        = @()
}

function Write-Report {
    param([int]$Code)
    $lines['exit_code'] = $Code
    if ($AsJson) {
        $lines | ConvertTo-Json -Depth 4
    } else {
        foreach ($key in $lines.Keys) {
            $value = $lines[$key]
            if ($value -is [array]) { $value = ($value -join ', ') }
            "{0,-13}: {1}" -f $key, $value
        }
    }
    exit $Code
}

try {
    # /api/tags is the only endpoint that proves both liveness and store identity.
    $tags = Invoke-RestMethod -Uri "$BaseUrl/api/tags" -TimeoutSec $TimeoutSec
    $lines['up'] = $true
    $lines['models'] = @($tags.models | ForEach-Object { $_.name })

    try {
        $lines['version'] = (Invoke-RestMethod -Uri "$BaseUrl/api/version" -TimeoutSec $TimeoutSec).version
    } catch {
        $lines['notes'] += "api/version unavailable: $($_.Exception.Message)"
    }

    $manifestRoot = Join-Path $ModelsDir 'manifests'
    if (Test-Path $manifestRoot) {
        $lines['store_models'] = @(
            Get-ChildItem -Path $manifestRoot -Recurse -File -ErrorAction SilentlyContinue |
                ForEach-Object { $_.FullName.Substring($manifestRoot.Length + 1) }
        )
    } else {
        $lines['notes'] += "no manifests under $ModelsDir"
    }

    if (-not ($lines['models'] | Where-Object { $_ -like "$Model*" })) {
        $lines['notes'] += "server is up but does not list '$Model': it reads a different"
        $lines['notes'] += "OLLAMA_MODELS than $ModelsDir - restarting it will not help,"
        $lines['notes'] += "the environment of its parent process must be fixed"
        if ($CheckAppPort -gt 0) {
            try {
                $app = Invoke-RestMethod -Uri "http://127.0.0.1:$CheckAppPort/api/tags" -TimeoutSec $TimeoutSec
                $appNames = @($app.models | ForEach-Object { $_.name })
                if (-not $appNames.Count -and $lines['store_models'].Count) {
                    $lines['notes'] += "WARNING: app server on $CheckAppPort lists no models"
                    $lines['notes'] += "while the store has them (same mismatch class)"
                }
            } catch {
                $lines['notes'] += "note: nothing answering on app port $CheckAppPort"
            }
        }
        Write-Report 2
    }

    Write-Report 0
} catch {
    $lines['notes'] += "probe failed: $($_.Exception.Message)"
    Write-Report 1
}
