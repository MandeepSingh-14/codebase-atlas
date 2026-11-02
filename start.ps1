# Run from PowerShell: .\start.ps1
param([int]$Port = 8767)
$ErrorActionPreference = 'Stop'
$projectPath = $PSScriptRoot
$pythonPath = Join-Path $projectPath '.venv\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $pythonPath)) {
    Write-Error 'Virtual environment missing. Run uv sync in this project first.'
}
$env:PYTHONPATH = Join-Path $projectPath 'src'
$env:PYTHONDONTWRITEBYTECODE = '1'
Set-Location -LiteralPath $projectPath
Write-Host "Cartographer: http://127.0.0.1:$Port"
Write-Host 'Keep this terminal open. Press Ctrl+C to stop. Restart after editing .env.'
& $pythonPath -B -m uvicorn codemap.api:app --host 127.0.0.1 --port $Port
