#Requires -Version 5.1
<#
  stop.ps1 - stop the backend started by scripts\start.ps1.

  Safety: only processes whose command line contains both "uvicorn" and
  "app.main:app" are stopped. A random process that happens to hold the port
  is reported, never killed.

  Usage:
    powershell -ExecutionPolicy Bypass -File scripts\stop.ps1
    powershell -ExecutionPolicy Bypass -File scripts\stop.ps1 -Port 8080
#>
[CmdletBinding()]
param(
  [int]$Port = 8000
)

$ErrorActionPreference = 'Stop'
try { [Console]::OutputEncoding = [System.Text.Encoding]::UTF8 } catch { }
$repo    = [System.IO.Path]::GetDirectoryName($PSScriptRoot)
$runDir  = Join-Path $repo '.run'
$pidFile = Join-Path $runDir 'api.pid'

function Test-OurUvicorn([int]$procId) {
  try {
    $cl = (Get-CimInstance Win32_Process -Filter "ProcessId=$procId").CommandLine
    return ($cl -and $cl -match 'uvicorn' -and $cl -match 'app\.main:app')
  } catch { return $false }
}

$target = $null

if (Test-Path -LiteralPath $pidFile) {
  $saved = (Get-Content -LiteralPath $pidFile -Raw).Trim()
  if ($saved -match '^\d+$' -and (Test-OurUvicorn ([int]$saved))) {
    $target = [int]$saved
  }
}

if (-not $target) {
  try {
    $listener = Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction Stop | Select-Object -First 1
    if ($listener -and (Test-OurUvicorn $listener.OwningProcess)) { $target = $listener.OwningProcess }
    elseif ($listener) {
      Write-Host "  !!  Port $Port is held by PID $($listener.OwningProcess), which is not this project's uvicorn. Not touching it." -ForegroundColor Yellow
      exit 1
    }
  } catch { }
}

if (-not $target) {
  Write-Host '  OK  Nothing to stop (no running instance found).' -ForegroundColor Green
  Remove-Item -LiteralPath $pidFile -ErrorAction SilentlyContinue
  exit 0
}

Write-Host "==> Stopping PID $target" -ForegroundColor Cyan
Stop-Process -Id $target -ErrorAction Stop
for ($i = 0; $i -lt 20; $i++) {
  Start-Sleep -Milliseconds 250
  if (-not (Get-Process -Id $target -ErrorAction SilentlyContinue)) { break }
}
if (Get-Process -Id $target -ErrorAction SilentlyContinue) {
  Write-Host '  !!  Graceful stop timed out, forcing.' -ForegroundColor Yellow
  Stop-Process -Id $target -Force -ErrorAction SilentlyContinue
}

# uvicorn on Windows starts a child process that actually owns the listening socket
# (log line: "Started server process [<pid>]"). Killing the parent normally takes the
# child down with it, but a crashed parent can leave an orphan holding the port.
# Fallback: if the port is still bound by this project's uvicorn, stop that process too.
$orphan = $null
try {
  $still = Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction Stop | Select-Object -First 1
  if ($still -and (Test-OurUvicorn $still.OwningProcess)) { $orphan = $still.OwningProcess }
} catch { }
if ($orphan) {
  Write-Host "  !!  Port $Port still held by PID $orphan (uvicorn child), stopping it." -ForegroundColor Yellow
  Stop-Process -Id $orphan -Force -ErrorAction SilentlyContinue
  Start-Sleep -Milliseconds 500
}

Remove-Item -LiteralPath $pidFile -ErrorAction SilentlyContinue
Write-Host '  OK  Stopped.' -ForegroundColor Green
exit 0
