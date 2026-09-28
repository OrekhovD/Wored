<#
.SYNOPSIS
    Run the WORED bonsai Ollama server in the FOREGROUND of this terminal.

.DESCRIPTION
    Owner's control requirement: the bonsai server must live in the Qoder
    terminal, visible, controllable with Ctrl+C.

    Memory-safe design (v2): the previous version streamed the child's
    stdout through Start-Job/Receive-Job, which buffered every log line in
    RAM and grew to 3.8 GB.  This version redirects the child's output to a
    log file and tails that file with Get-Content -Wait — constant memory.

      1. Refuses to double-start (prints who owns the port instead).
      2. Starts `ollama serve` as a child with output -> logs\bonsai_serve.log.
      3. Tails the log live in this terminal. Ctrl+C stops the server
         cleanly and releases the port.

    Scheduled-task guard WORED-Bonsai-8088 must be disabled in foreground
    mode (two supervisors fight over the port otherwise):
      Unregister-ScheduledTask -TaskName 'WORED-Bonsai-8088' -Confirm:$false
#>
[CmdletBinding()]
param(
    [int]$Port = 8088,
    [string]$ModelsDir = "$env:USERPROFILE\.ollama\models",
    [string]$Model = 'bonsai-27b:lmstudio-q1',
    [ValidateRange(2048, 262144)]
    [int]$ContextLength = 8192,
    [string]$KeepAlive = '30m'
)

$ErrorActionPreference = 'Stop'
$BaseUrl = "http://127.0.0.1:$Port"
$repoRoot = Split-Path -Parent $PSScriptRoot
$LogFile = Join-Path $repoRoot 'logs\bonsai_serve.log'

function Test-ServerUp {
    param([string]$Url)
    try { $null = Invoke-RestMethod -Uri "$Url/api/tags" -TimeoutSec 3; return $true }
    catch { return $false }
}

# ── preflight ────────────────────────────────────────────────────────────
$ollamaExe = Join-Path $env:LOCALAPPDATA 'Programs\Ollama\ollama.exe'
if (-not (Test-Path $ollamaExe)) { throw "ollama.exe not found: $ollamaExe" }
if (-not (Test-Path (Join-Path $ModelsDir 'manifests'))) {
    throw "model store not found or empty: $ModelsDir"
}
$logDir = Split-Path -Parent $LogFile
if (-not (Test-Path $logDir)) { New-Item -ItemType Directory -Path $logDir -Force | Out-Null }

# ── adopt-or-abort: never double-start ───────────────────────────────────
if (Test-ServerUp -Url $BaseUrl) {
    $owner = (Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue |
              Select-Object -First 1).OwningProcess
    $ownerName = if ($owner) { (Get-Process -Id $owner -ErrorAction SilentlyContinue).ProcessName } else { '?' }
    Write-Host ""
    Write-Host "server ALREADY RUNNING on $BaseUrl (pid $owner, $ownerName)" -ForegroundColor Yellow
    Write-Host "  refusing to double-start. stop it first: .\scripts\bonsai_ctl.ps1 stop" -ForegroundColor Yellow
    return
}

# ── environment ──────────────────────────────────────────────────────────
$env:OLLAMA_HOST = "127.0.0.1:$Port"
$env:OLLAMA_MODELS = $ModelsDir
$env:OLLAMA_CONTEXT_LENGTH = "$ContextLength"
$env:OLLAMA_KEEP_ALIVE = $KeepAlive

Write-Host ""
Write-Host "  bonsai-27b FOREGROUND SERVER" -ForegroundColor Cyan
Write-Host "  endpoint : $BaseUrl" -ForegroundColor Gray
Write-Host "  store    : $ModelsDir" -ForegroundColor Gray
Write-Host "  context  : $ContextLength, keep-alive $KeepAlive" -ForegroundColor Gray
Write-Host "  logfile  : $LogFile" -ForegroundColor Gray
Write-Host "  stop     : Ctrl+C (clean shutdown)" -ForegroundColor Gray
Write-Host ""

# ── start child with output to file (constant memory) ───────────────────
$proc = Start-Process -FilePath $ollamaExe -ArgumentList 'serve' `
    -RedirectStandardOutput $LogFile -RedirectStandardError "$LogFile.err" `
    -NoNewWindow -PassThru
Write-Host "started ollama serve (pid $($proc.Id)), logs -> $LogFile" -ForegroundColor Green
Write-Host ""

# ── tail the log until the child exits ───────────────────────────────────
try {
    # Show the file growing live; Get-Content -Wait is a constant-memory tail
    Get-Content -Path $LogFile -Wait -Tail 1000 | ForEach-Object { Write-Host $_ }
} finally {
    if (-not $proc.HasExited) {
        Write-Host "stopping ollama serve (pid $($proc.Id))..." -ForegroundColor Yellow
        Stop-Process -Id $proc.Id -Force -ErrorAction SilentlyContinue
        # also stop the model runner llama-server spawned by this instance
        Get-CimInstance Win32_Process -Filter "Name='llama-server.exe'" |
            Where-Object { (Get-CimInstance Win32_Process -Filter "ProcessId=$($_.ParentProcessId)" -ErrorAction SilentlyContinue).ProcessId -eq $proc.Id } |
            ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }
        $proc.WaitForExit(5000) | Out-Null
    }
    if (-not (Test-ServerUp -Url $BaseUrl)) {
        Write-Host "server stopped — port $Port released" -ForegroundColor Green
    }
}