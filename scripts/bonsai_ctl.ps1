<#
.SYNOPSIS
    Control switch for the WORED bonsai server: start | stop | status | restart.

.DESCRIPTION
    Companion to bonsai_fg.ps1 (foreground mode).  Commands:

      status  — is the server up? which process owns the port? models in VRAM?
      stop    — stop the process listening on :8088 (only if it is ollama.exe)
      logs    — show the last state written by the supervision tools

    Safety: `stop` refuses to kill anything that is not ollama.exe.

.EXAMPLE
    .\scripts\bonsai_ctl.ps1 status
    .\scripts\bonsai_ctl.ps1 stop
#>
[CmdletBinding()]
param(
    [Parameter(Position = 0)]
    [ValidateSet('status', 'stop')]
    [string]$Command = 'status',
    [int]$Port = 8088
)

$BaseUrl = "http://127.0.0.1:$Port"

function Get-PortOwner {
    $conn = Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue |
            Select-Object -First 1
    if (-not $conn) { return $null }
    $proc = Get-Process -Id $conn.OwningProcess -ErrorAction SilentlyContinue
    [pscustomobject]@{ Pid = $conn.OwningProcess; Name = $proc.ProcessName; Path = $proc.Path }
}

switch ($Command) {
    'status' {
        $owner = Get-PortOwner
        if (-not $owner) {
            Write-Host "bonsai server DOWN (nothing listens on :$Port)" -ForegroundColor Red
            Write-Host "start:  .\scripts\bonsai_fg.ps1" -ForegroundColor Yellow
            return
        }
        Write-Host "bonsai server UP on :$Port" -ForegroundColor Green
        Write-Host "  pid $($owner.Pid) — $($owner.Name)"
        if ($owner.Name -ne 'ollama') {
            Write-Host "  WARNING: port owned by '$($owner.Name)', not ollama!" -ForegroundColor Red
        }
        try {
            $ps = Invoke-RestMethod -Uri "$BaseUrl/api/ps" -TimeoutSec 5
            foreach ($m in $ps.models) {
                $sizeMB = [math]::Round($m.size_vram / 1MB)
                Write-Host "  in VRAM: $($m.name) ($sizeMB MB), expires: $($m.expires_at)" -ForegroundColor Gray
            }
            if (-not $ps.models) { Write-Host "  no model loaded in VRAM (cold)" -ForegroundColor DarkGray }
        } catch { Write-Host "  api/ps failed: $($_.Exception.Message)" -ForegroundColor DarkGray }
    }

    'stop' {
        $owner = Get-PortOwner
        if (-not $owner) { Write-Host "nothing to stop — :$Port is free" -ForegroundColor Yellow; return }
        if ($owner.Name -ne 'ollama') {
            Write-Host "REFUSED: port owned by '$($owner.Name)' (pid $($owner.Pid)), not ollama" -ForegroundColor Red
            return
        }
        Write-Host "stopping ollama serve (pid $($owner.Pid))..." -ForegroundColor Yellow
        Stop-Process -Id $owner.Pid -Force
        Start-Sleep -Seconds 2
        if (Get-PortOwner) { Write-Host "still listening — manual check needed" -ForegroundColor Red }
        else { Write-Host "stopped — port $Port released" -ForegroundColor Green }
    }
}