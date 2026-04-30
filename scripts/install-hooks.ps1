# Install git pre-commit hook

$repo_root = (git rev-parse --show-toplevel)
if (-not $?) {
    Write-Error "Script must be run inside git repository."
    exit 1
}

$hooks_dir = Join-Path $repo_root ".git\hooks"
if (-not (Test-Path $hooks_dir)) {
    Write-Error ".git/hooks directory not found."
    exit 1
}

$hook_path = Join-Path $hooks_dir "pre-commit"
$script_path = "scripts/pre-commit-check-env.sh"

$hook_content = @"
#!/bin/bash
# Installed by scripts/install-hooks.ps1
bash $script_path
"@

Set-Content -Path $hook_path -Value $hook_content -Encoding ascii
Write-Host "Pre-commit hook installed successfully."
Write-Host "Comitting .env and .env.postgres files will be blocked."
