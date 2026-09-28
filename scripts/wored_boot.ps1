<#
.SYNOPSIS
    WORED startup sequence: Ollama bonsai server + Docker, visible in the Qoder IDE terminal.

.DESCRIPTION
    Owner's boot sequence requirement (2026-09-28):
      1. Start Qoder IDE with the last chat (VS Code-like restore handles the
         chat when the same workspace is opened; D:\WORED is the workspace).
      2. Inside Qoder's integrated terminal — start the local Ollama server
         on 127.0.0.1:8088 serving bonsai-27b:lmstudio-q1 (visible status).
      3. Start Docker Desktop if it is not running, then wait for the engine.

    The old WORED-Bonsai-8088 scheduled task flashed a hidden console every
    5 minutes.  This replaces it: the server status lives in a real terminal
    tab inside Qoder where the owner works.

.USAGE
    powershell -ExecutionPolicy Bypass -File scripts\wored_boot.ps1
    (or pin it to a Windows scheduled task at logon / Start Menu shortcut)
#>
[CmdletBinding()]
param(
    # Path to the workspace Qoder must open (restores the last chat for it)
    [string]$Workspace = 'D:\WORED',
    # Ollama server parameters (must match WORED webui expectations)
    [int]$Port = 8088,
    [string]$Model = 'bonsai-27b:lmstudio-q1',
    [string]$ModelsDir = "$env:USERPROFILE\.ollama\models"
)

$ErrorActionPreference = 'Continue'
$banner = @'
  ____        _          ____  _____ ____
 / ___|  ___ | | ___ ___/ ___||  _ \ ___
 \___ \ / _ \/ __|/ __\___ \ | |_) | __|
  ___) | (_) \__ \\__ \ ___) |  _ < ___|
 |____/ \___/|___/|___/____/|____ \___|

 WORED boot: bonsai server + docker
'@
Write-Host $banner -ForegroundColor Cyan

# ── Step 0: tool paths ──────────────────────────────────────────────────
$ollamaPath = Join-Path $env:LOCALAPPDATA 'Programs\Ollama'
if (-not (Get-Command ollama -ErrorAction SilentlyContinue)) {
    if (Test-Path (Join-Path $ollamaPath 'ollama.exe')) {
        $env:Path = "$ollamaPath;$env:Path"
    }
}
$dockerCli = Join-Path $env:LOCALAPPDATA 'Programs\Docker Desktop\resources\bin\docker.exe'
if (-not (Get-Command docker -ErrorAction SilentlyContinue)) {
    $dockerCli = Join-Path $env:ProgramFiles 'Docker\Docker\resources\bin'
    if (Test-Path $dockerCli) { $env:Path = "$dockerCli;$env:Path" }
}

# ── Step 1: Ollama bonsai server ────────────────────────────────────────
Write-Host "`n[1/3] Bonsai server (ollama serve)" -ForegroundColor Yellow
$BaseUrl = "http://127.0.0.1:$Port"
$up = $false
try {
    $resp = Invoke-WebRequest -Uri "$BaseUrl/api/tags" -TimeoutSec 3 -UseBasicParsing -ErrorAction Stop
    $models = ($resp.Content | ConvertFrom-Json).models | ForEach-Object { $_.name }
    Write-Host "  already UP on $Port (models: $($models -join ', '))" -ForegroundColor Green
    $up = $true
} catch {
    Write-Host "  port $Port is down — starting ollama serve" -ForegroundColor Yellow
    $env:OLLAMA_HOST = "127.0.0.1:$Port"
    $env:OLLAMA_MODELS = $ModelsDir
    $env:OLLAMA_CONTEXT_LENGTH = '8192'
    $env:OLLAMA_KEEP_ALIVE = '30m'
    Start-Process -FilePath (Join-Path $ollamaPath 'ollama.exe') -ArgumentList 'serve' -WindowStyle Hidden
    for ($i = 0; $i -lt 20; $i++) {
        Start-Sleep -Seconds 2
        try {
            $resp = Invoke-WebRequest -Uri "$BaseUrl/api/tags" -TimeoutSec 3 -ErrorAction Stop
            $up = $true
            break
        } catch { Write-Host "  waiting for server... ($($i+1)/20)" -ForegroundColor DarkGray }
    }
}
if ($up) {
    # Warm the model into VRAM so the first forecast doesn't pay the load cost
    Write-Host "  warming bonsai-27b (loads ~4 GB into VRAM)..." -ForegroundColor DarkGray
    $warm = & ollama run --keep-alive 30m bonsai-27b:lmstudio-q1 "" 2>&1
    Write-Host "  bonsai server READY" -ForegroundColor Green
} else {
    Write-Host "  bonsai server FAILED to start — check ollama manually" -ForegroundColor Red
}

# ── Step 2: Docker Desktop + engine ─────────────────────────────────────
Write-Host "`n[2/3] Docker Desktop" -ForegroundColor Yellow
$dockerRunning = $false
try {
    $info = & docker info --format '{{.ServerVersion}}' 2>$null
    if ($LASTEXITCODE -eq 0 -and $info) {
        $dockerRunning = $true
        Write-Host "  engine already UP (v$info)" -ForegroundColor Green
    }
} catch { }

if (-not $dockerRunning) {
    $dd = Join-Path $env:LOCALAPPDATA 'Docker\Docker Desktop.exe'
    if (-not (Test-Path $dd)) { $dd = Join-Path $env:ProgramFiles 'Docker\Docker\Docker Desktop.exe' }
    if (Test-Path $dd) {
        Write-Host "  starting Docker Desktop..." -ForegroundColor Yellow
        Start-Process $dd
        for ($i = 0; $i -lt 40; $i++) {
            Start-Sleep -Seconds 3
            & docker info --format '{{.ServerVersion}}' *>$null
            if ($LASTEXITCODE -eq 0) { $dockerRunning = $true; break }
            Write-Host "  waiting for engine... ($(($i+1)*3)s)" -ForegroundColor DarkGray
        }
    } else {
        Write-Host "  Docker Desktop not found — install it or fix the path" -ForegroundColor Red
    }
}
if ($dockerRunning) {
    $containers = & docker ps -a --filter 'name=htx_trading_bot' --format '{{.Names}} {{.Status}}'
    Write-Host "  Docker READY. WORED containers:" -ForegroundColor Green
    $containers | ForEach-Object { Write-Host "    $_" -ForegroundColor DarkGray }
} else {
    Write-Host "  Docker engine did not come up in 120s — WORED containers will not start" -ForegroundColor Red
}

# ── Step 3: status summary ──────────────────────────────────────────────
Write-Host "`n[3/3] Status" -ForegroundColor Yellow
$gpu = & nvidia-smi --query-gpu=memory.used,memory.free,utilization.gpu --format=csv,noheader 2>$null
Write-Host "  GPU: $gpu" -ForegroundColor DarkGray
Write-Host "  Bonsai endpoint: $BaseUrl (think=false: ~17s/role, think=true: ~150s)" -ForegroundColor Gray
Write-Host "`nWORED boot complete. Terminal stays open for supervision." -ForegroundColor Cyan