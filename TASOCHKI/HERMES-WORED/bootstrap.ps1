[CmdletBinding()]
param([string]$PythonExecutable = '')
$ErrorActionPreference = 'Stop'
$DeliveryRoot = $PSScriptRoot
$VenvPython = Join-Path $DeliveryRoot '.venv\Scripts\python.exe'

function Test-Python311([string]$Executable) {
    if (-not (Test-Path -LiteralPath $Executable -PathType Leaf)) { return $false }
    & $Executable -c 'import sys; sys.exit(0 if sys.version_info[:2] == (3,11) else 1)' 2>$null
    return ($LASTEXITCODE -eq 0)
}

if (-not (Test-Path -LiteralPath $VenvPython)) {
    $Candidates = @()
    if ($PythonExecutable) { $Candidates += $PythonExecutable }
    else {
        $Candidates += (Join-Path $env:APPDATA 'uv\python\cpython-3.11-windows-x86_64-none\python.exe')
        $Candidates += (Join-Path $env:LOCALAPPDATA 'Programs\Python\Python311\python.exe')
        $Candidates += (Join-Path $env:LOCALAPPDATA 'Python\pythoncore-3.11-64\python.exe')
        $PathPython = Get-Command python.exe -ErrorAction SilentlyContinue
        if ($PathPython) { $Candidates += $PathPython.Source }
    }
    $SelectedPython = $null
    foreach ($Candidate in $Candidates) {
        if (Test-Python311 $Candidate) { $SelectedPython = $Candidate; break }
    }
    if (-not $SelectedPython) { throw 'PYTHON311_REQUIRED: run bootstrap.ps1 -PythonExecutable with the absolute path to Python 3.11.' }
    & $SelectedPython -m venv (Join-Path $DeliveryRoot '.venv')
    if ($LASTEXITCODE -ne 0) { throw 'QA_VENV_CREATION_FAILED' }
}
if (-not (Test-Python311 $VenvPython)) { throw 'QA_VENV_VERSION_MISMATCH: Python 3.11 is required; existing environment was preserved.' }
& $VenvPython -m pip --version
if ($LASTEXITCODE -ne 0) {
    & $VenvPython -m ensurepip
    if ($LASTEXITCODE -ne 0) { throw 'QA_PIP_UNAVAILABLE' }
}
& $VenvPython -m pip install --disable-pip-version-check -r (Join-Path $DeliveryRoot 'requirements-qa.txt')
if ($LASTEXITCODE -ne 0) { throw 'QA_DEPENDENCIES_UNAVAILABLE' }
& $VenvPython -m pip check
if ($LASTEXITCODE -ne 0) { throw 'QA_DEPENDENCY_CONFLICT' }
& $VenvPython -B (Join-Path $DeliveryRoot 'tools\run_sql_qa.py') --help
if ($LASTEXITCODE -ne 0) { throw 'QA_HELPER_IMPORT_FAILED' }
Write-Output "QA environment ready: $VenvPython"
