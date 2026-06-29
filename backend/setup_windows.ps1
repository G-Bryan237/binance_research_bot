# One-command setup for Binance Research Bot on Windows PowerShell
$ErrorActionPreference = 'Stop'

Set-Location -Path $PSScriptRoot

if (-not (Get-Command py -ErrorAction SilentlyContinue)) {
  Write-Error "Python launcher 'py' not found. Install Python 3.10+ and retry."
}
if (-not (Get-Command npm -ErrorAction SilentlyContinue)) {
  Write-Error "npm not found. Install Node.js LTS and retry."
}

if (-not (Test-Path .venv)) {
  py -m venv .venv
}

& .\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
pip install -r requirements.txt

Push-Location frontend
npm install
Pop-Location

Write-Host "Setup complete."
Write-Host "Run API with: python run_api.py"
Write-Host "Run paper mode with: python run_paper.py"
Write-Host "Run worker with: python run_worker.py"
