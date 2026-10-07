$ErrorActionPreference = "Stop"
Set-Location (Split-Path $PSScriptRoot -Parent)

Write-Host "[PowerLab] Windows setup (Python 3.14 compatible)" -ForegroundColor Cyan

$PythonArgs = $null
if (Get-Command py -ErrorAction SilentlyContinue) {
    & py -3.14 --version *> $null
    if ($LASTEXITCODE -eq 0) { $PythonArgs = @("py", "-3.14") }
    else { $PythonArgs = @("py", "-3") }
} elseif (Get-Command python -ErrorAction SilentlyContinue) {
    $PythonArgs = @("python")
} else {
    throw "Python wurde nicht gefunden. Python 3.14 ist fuer PowerLab geeignet."
}

if (-not (Test-Path ".venv")) {
    Write-Host "[1/4] Erzeuge virtuelle Umgebung .venv ..."
    if ($PythonArgs[0] -eq "py") { & py $PythonArgs[1] -m venv .venv }
    else { & python -m venv .venv }
} else {
    Write-Host "[1/4] .venv existiert bereits."
}

$Py = Join-Path $PWD ".venv\Scripts\python.exe"
if (-not (Test-Path $Py)) { throw "Virtuelle Umgebung konnte nicht erzeugt werden." }

Write-Host "[2/4] Aktualisiere pip/setuptools/wheel ..."
& $Py -m pip install --upgrade pip setuptools wheel

Write-Host "[3/4] Installiere Runtime- und Test-Abhaengigkeiten ..."
& $Py -m pip install -r requirements.txt -r requirements-dev.txt

if (-not (Test-Path ".env")) {
    Copy-Item ".env.example" ".env"
    Write-Host "[4/4] .env aus .env.example erzeugt."
} else {
    Write-Host "[4/4] .env existiert bereits und wurde nicht ueberschrieben."
}

Write-Host ""
& $Py --version
Write-Host "Setup abgeschlossen." -ForegroundColor Green
Write-Host "Naechste Schritte:"
Write-Host "  .\scripts\run_windows.ps1"
Write-Host "Danach: http://127.0.0.1:8000"
