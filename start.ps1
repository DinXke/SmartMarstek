#Requires -Version 5.1
$RootDir    = $PSScriptRoot
$BackendDir = Join-Path $RootDir "backend"

if (-not (Test-Path (Join-Path $BackendDir "venv"))) {
    Write-Host "[ERROR] Not installed yet. Run install.ps1 first." -ForegroundColor Red
    Read-Host "Press Enter to exit"; exit 1
}
if (-not (Test-Path (Join-Path $RootDir "frontend\dist"))) {
    Write-Host "[ERROR] Frontend not built. Run install.ps1 first." -ForegroundColor Red
    Read-Host "Press Enter to exit"; exit 1
}

Write-Host "Starting Marstek Dashboard at http://localhost:5000 ..." -ForegroundColor Cyan
Write-Host "Press Ctrl+C to stop.`n"

# Open browser after 2 s
Start-Job {
    Start-Sleep 2
    Start-Process "http://localhost:5000"
} | Out-Null

Set-Location $BackendDir
& "venv\Scripts\python.exe" app.py
