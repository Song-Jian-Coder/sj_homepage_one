#Requires -Version 5.1
<#
  bootstrap.ps1 - create backend\.venv and install runtime + test dependencies.

  Why a venv: the project has no global pytest; the API only needs fastapi/uvicorn/pymysql.
  A local venv keeps ETL/API deps reproducible and lets scripts\check.ps1 run the test suite.
  start.ps1 prefers backend\.venv automatically once it exists.

  Usage:
    powershell -ExecutionPolicy Bypass -File scripts\bootstrap.ps1
    powershell -ExecutionPolicy Bypass -File scripts\bootstrap.ps1 -Force     # recreate
    powershell -ExecutionPolicy Bypass -File scripts\bootstrap.ps1 -PythonExe C:\...\python.exe
#>
[CmdletBinding()]
param(
  [switch]$Force,
  [switch]$SkipTests,
  [string]$PythonExe
)

$ErrorActionPreference = 'Stop'
try { [Console]::OutputEncoding = [System.Text.Encoding]::UTF8 } catch { }
$repo    = [System.IO.Path]::GetDirectoryName($PSScriptRoot)
$backend = Join-Path $repo 'backend'
$venv    = Join-Path $backend '.venv'
$venvPy  = Join-Path $venv 'Scripts\python.exe'

function Write-Step($m) { Write-Host "==> $m" -ForegroundColor Cyan }
function Fail($m) { Write-Host "  XX  $m" -ForegroundColor Red; exit 1 }

if ($Force -and (Test-Path -LiteralPath $venv)) {
  Write-Step 'Removing existing backend\.venv (-Force)'
  Remove-Item -LiteralPath $venv -Recurse -Force
}

Write-Step 'Locating base Python'
$lib = Join-Path $PSScriptRoot 'lib-python.ps1'
if (-not (Test-Path -LiteralPath $lib)) { Fail "Missing helper: $lib" }
. $lib
$pyBase = Find-PythonExe -Explicit $PythonExe -BackendRoot $backend
if (-not $pyBase) { Fail 'No Python interpreter found. Install Python 3.11+ first.' }
$PythonExe = $pyBase
Write-Host "  using $PythonExe"

if (-not (Test-Path -LiteralPath $venvPy)) {
  Write-Step "Creating venv at $venv"
  & $PythonExe -m venv $venv
  if ($LASTEXITCODE -ne 0) { Fail 'venv creation failed.' }
} else {
  Write-Host "  venv already exists: $venv"
}

Write-Step 'Installing dependencies'
& $venvPy -m pip install --upgrade pip --quiet
if ($LASTEXITCODE -ne 0) { Write-Host '  (pip upgrade failed, continuing)' -ForegroundColor Yellow }

& $venvPy -m pip install -r (Join-Path $backend 'requirements.txt')
if ($LASTEXITCODE -ne 0) { Fail 'pip install -r backend\requirements.txt failed.' }

if ($SkipTests) {
  Write-Step 'Removing test-only packages (-SkipTests)'
  & $venvPy -m pip uninstall -y pytest httpx --quiet 2>$null | Out-Null
}

Write-Step 'Verifying imports'
& $venvPy -c "import fastapi, uvicorn, pymysql, dotenv; print('runtime deps OK')"
if ($LASTEXITCODE -ne 0) { Fail 'runtime deps import failed.' }
if (-not $SkipTests) {
  & $venvPy -c "import pytest, httpx; print('test deps OK')"
  if ($LASTEXITCODE -ne 0) { Fail 'test deps import failed.' }
}

Write-Host ''
Write-Host '  Done. Next:' -ForegroundColor Green
Write-Host '    powershell -ExecutionPolicy Bypass -File scripts\start.ps1'
Write-Host '    powershell -ExecutionPolicy Bypass -File scripts\check.ps1'
exit 0
