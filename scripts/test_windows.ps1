$ErrorActionPreference = "Stop"
Set-Location (Split-Path $PSScriptRoot -Parent)
$Py = Join-Path $PWD ".venv\Scripts\python.exe"
if (-not (Test-Path $Py)) { throw "Keine .venv gefunden. Fuehre zuerst setup_windows.ps1 aus." }
& $Py -m pytest -q
