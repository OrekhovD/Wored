# hermes/hermes.ps1 — PowerShell обёртка для запуска Hermes Agent через WSL2
# Использование: .\hermes\hermes.ps1 [аргументы]
# Или добавь алиас: Set-Alias hermes "D:\WORED\hermes\hermes.ps1"

$args_str = $args -join " "

if ($args_str -eq "") {
    # Без аргументов — запуск TUI
    wsl -d Ubuntu-24.04 -- bash --login -c "cd /mnt/d/WORED && hermes --tui"
} else {
    wsl -d Ubuntu-24.04 -- bash --login -c "cd /mnt/d/WORED && hermes $args_str"
}
