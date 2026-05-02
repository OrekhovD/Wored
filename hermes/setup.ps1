# =============================================================================
# hermes/setup.ps1
# PowerShell launcher для установки Hermes Agent (запускает через WSL2).
# Использование: .\hermes\setup.ps1
# =============================================================================

$ErrorActionPreference = "Stop"

Write-Host ""
Write-Host "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━" -ForegroundColor Cyan
Write-Host "  Hermes Agent Setup — WORED (PowerShell Launcher)" -ForegroundColor Cyan
Write-Host "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━" -ForegroundColor Cyan
Write-Host ""

# ─── Проверка WSL2 ───────────────────────────────────────────────────────────
Write-Host "[1/4] Проверка WSL2..." -ForegroundColor Yellow

$wslList = wsl --list --quiet 2>$null
$hasUbuntu = $wslList | Where-Object { $_ -match "Ubuntu" }

if (-not $hasUbuntu) {
    Write-Host ""
    Write-Host "  Ubuntu не установлен в WSL2." -ForegroundColor Red
    Write-Host "  Hermes Agent требует Linux (WSL2)." -ForegroundColor Red
    Write-Host ""
    Write-Host "  Для установки выполни:" -ForegroundColor Yellow
    Write-Host "    wsl --install -d Ubuntu-24.04" -ForegroundColor White
    Write-Host ""
    Write-Host "  После установки перезагрузи компьютер и запусти этот скрипт снова." -ForegroundColor Yellow
    Write-Host ""

    $install = Read-Host "Установить Ubuntu-24.04 сейчас? (y/n)"
    if ($install -eq "y") {
        Write-Host "Запускаю установку Ubuntu-24.04..." -ForegroundColor Green
        wsl --install -d Ubuntu-24.04
        Write-Host ""
        Write-Host "После перезагрузки запусти: .\hermes\setup.ps1" -ForegroundColor Yellow
        exit 0
    }
    exit 1
}

Write-Host "  Ubuntu найден в WSL2" -ForegroundColor Green

# ─── Проверка Docker доступности из WSL ──────────────────────────────────────
Write-Host "[2/4] Проверка Docker в WSL..." -ForegroundColor Yellow

$dockerCheck = wsl -d Ubuntu -- bash -c "docker --version 2>/dev/null" 2>$null
if ($dockerCheck) {
    Write-Host "  Docker доступен из WSL: $dockerCheck" -ForegroundColor Green
} else {
    Write-Host "  Docker не доступен из WSL." -ForegroundColor Red
    Write-Host "  Убедись что в Docker Desktop включена опция:" -ForegroundColor Yellow
    Write-Host "    Settings → Resources → WSL Integration → Ubuntu" -ForegroundColor White
    Write-Host ""
}

# ─── Конвертация пути проекта для WSL ────────────────────────────────────────
Write-Host "[3/4] Подготовка путей..." -ForegroundColor Yellow

$projectDir = (Get-Item "$PSScriptRoot\..").FullName
$wslProjectDir = wsl -d Ubuntu -- wslpath -a "$projectDir" 2>$null

if (-not $wslProjectDir) {
    # Fallback: manual conversion D:\WORED → /mnt/d/WORED
    $drive = $projectDir.Substring(0, 1).ToLower()
    $rest = $projectDir.Substring(2).Replace('\', '/')
    $wslProjectDir = "/mnt/$drive$rest"
}

Write-Host "  Windows: $projectDir" -ForegroundColor Gray
Write-Host "  WSL:     $wslProjectDir" -ForegroundColor Gray

# ─── Запуск bash setup.sh через WSL ─────────────────────────────────────────
Write-Host "[4/4] Запускаю hermes/setup.sh через WSL..." -ForegroundColor Yellow
Write-Host ""

wsl -d Ubuntu -- bash "$wslProjectDir/hermes/setup.sh"

if ($LASTEXITCODE -eq 0) {
    Write-Host ""
    Write-Host "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━" -ForegroundColor Green
    Write-Host "  Setup завершен!" -ForegroundColor Green
    Write-Host "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━" -ForegroundColor Green
    Write-Host ""
    Write-Host "  Запуск Hermes из PowerShell:" -ForegroundColor Yellow
    Write-Host "    wsl -d Ubuntu -- bash $wslProjectDir/hermes/start.sh" -ForegroundColor White
    Write-Host ""
    Write-Host "  Или из WSL терминала:" -ForegroundColor Yellow
    Write-Host "    cd $wslProjectDir && bash hermes/start.sh" -ForegroundColor White
    Write-Host ""
} else {
    Write-Host ""
    Write-Host "  Setup завершился с ошибкой. Проверь вывод выше." -ForegroundColor Red
    Write-Host ""
}
