# One-time setup: Python packages, your config file, and a first data refresh.
# Run from the project folder:  powershell -ExecutionPolicy Bypass -File setup\install.ps1

$ErrorActionPreference = "Stop"
$root = Split-Path $PSScriptRoot -Parent
Set-Location $root

if (-not (Get-Command python -ErrorAction SilentlyContinue)) {
    Write-Host "Python isn't installed. Get Python 3.12 or newer from python.org (tick 'Add to PATH'), then run this again."
    exit 1
}

if (-not (Test-Path ".venv")) {
    Write-Host "Creating virtual environment..."
    python -m venv .venv
}
Write-Host "Installing packages (FastF1 is large, this can take a few minutes)..."
& .\.venv\Scripts\python.exe -m pip install --upgrade pip --quiet
& .\.venv\Scripts\python.exe -m pip install -r requirements.txt --quiet

if (-not (Test-Path "config.toml")) {
    Copy-Item "config.example.toml" "config.toml"
    Write-Host "Created config.toml. Paste your Google Calendar secret iCal link into it."
}

Write-Host "Fetching data once..."
& .\.venv\Scripts\python.exe fetch.py --summary --no-race --no-render

Write-Host ""
Write-Host "Done. Next:"
Write-Host "  1. Edit config.toml (calendar link, favourite driver)."
Write-Host "  2. Run setup\register-tasks.ps1 to refresh automatically (this sets your desktop picture)."
Write-Host "  3. During F1 sessions, open http://localhost:8765 for live timing."
