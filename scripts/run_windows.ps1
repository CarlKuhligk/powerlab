$ErrorActionPreference = "Stop"
Set-Location (Split-Path $PSScriptRoot -Parent)

$Py = Join-Path $PWD ".venv\Scripts\python.exe"
if (-not (Test-Path $Py)) {
    throw "Keine .venv gefunden. Fuehre zuerst .\scripts\setup_windows.ps1 aus."
}

Write-Host "[PowerLab] Backend + Web-GUI" -ForegroundColor Cyan
Write-Host "URL: http://127.0.0.1:8000"
Write-Host "API: http://127.0.0.1:8000/docs"
Write-Host "Beenden mit Ctrl+C"
& $Py -m uvicorn app.main:app --host 127.0.0.1 --port 8000 --reload
