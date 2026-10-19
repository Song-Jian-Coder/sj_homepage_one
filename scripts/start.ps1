#Requires -Version 5.1
<#
  start.ps1 - start the Shenwan industry fund-flow dashboard backend.

  What it does (fail fast, never guesses):
    1. locate a usable Python interpreter (backend\.venv -> repo\.venv -> PATH -> known install path);
    2. verify runtime deps (fastapi / uvicorn / pymysql / python-dotenv);
    3. verify backend\.env exists (copy .env.example if missing, then stop and tell the operator);
    4. refuse to start if the port is taken by something that is not this project;
    5. start uvicorn and wait until /api/v1/health answers, then print the URLs.

  Usage:
    powershell -ExecutionPolicy Bypass -File scripts\start.ps1
    powershell -ExecutionPolicy Bypass -File scripts\start.ps1 -Port 8080
    powershell -ExecutionPolicy Bypass -File scripts\start.ps1 -Foreground   # keep logs on the console
    powershell -ExecutionPolicy Bypass -File scripts\start.ps1 -Force        # stop our own stale instance first

  Exit codes: 0 started / already running; 1 failed precheck or failed to become healthy.
#>
[CmdletBinding()]
param(
  [int]$Port = 8000,
  [string]$BindHost = '127.0.0.1',
  [switch]$Foreground,
  [switch]$Force,
  [string]$PythonExe
)

$ErrorActionPreference = 'Stop'
try { [Console]::OutputEncoding = [System.Text.Encoding]::UTF8 } catch { }
$repo    = [System.IO.Path]::GetDirectoryName($PSScriptRoot)
$backend = Join-Path $repo 'backend'
$runDir  = Join-Path $repo '.run'
$pidFile = Join-Path $runDir 'api.pid'

function Write-Step($msg) { Write-Host "==> $msg" -ForegroundColor Cyan }
function Write-Ok($msg)   { Write-Host "  OK  $msg" -ForegroundColor Green }
function Write-Warn2($msg){ Write-Host "  !!  $msg" -ForegroundColor Yellow }
function Fail($msg) {
  Write-Host "  XX  $msg" -ForegroundColor Red
  exit 1
}

# Surface the failing line instead of an opaque ParameterBindingValidationException.
trap {
  Write-Host "  XX  Unhandled error: $($_.Exception.Message)" -ForegroundColor Red
  if ($_.ScriptStackTrace) { Write-Host $_.ScriptStackTrace -ForegroundColor DarkGray }
  exit 1
}

# ---------------------------------------------------------------- 1. interpreter
Write-Step 'Locating Python interpreter'
$lib = Join-Path $PSScriptRoot 'lib-python.ps1'
if (-not (Test-Path -LiteralPath $lib)) { Fail "Missing helper: $lib" }
. $lib

$py = Find-PythonExe -Explicit $PythonExe -RepoRoot $repo -BackendRoot $backend
if (-not $py) {
  Fail 'No usable Python found. Install Python 3.11+ or run: powershell -File scripts\bootstrap.ps1'
}
Write-Ok $py

# ---------------------------------------------------------------- 2. dependencies
Write-Step 'Checking runtime dependencies'
$depCheck = & $py -c "import fastapi, uvicorn, pymysql, dotenv; print('ok')" 2>&1
if ($LASTEXITCODE -ne 0) {
  Write-Host $depCheck
  Fail "Missing Python packages. Run: & '$py' -m pip install -r backend\requirements.txt  (or scripts\bootstrap.ps1)"
}
Write-Ok 'fastapi / uvicorn / pymysql / python-dotenv'

# ---------------------------------------------------------------- 3. config
Write-Step 'Checking backend\.env'
$envFile = Join-Path $backend '.env'
if (-not (Test-Path -LiteralPath $envFile)) {
  $tpl = Join-Path $repo '.env.example'
  if (Test-Path -LiteralPath $tpl) {
    Copy-Item -LiteralPath $tpl -Destination $envFile
    Write-Warn2 "backend\.env was missing - copied the template to $envFile"
    Fail 'Fill in DB_PASSWORD / ADMIN_KEY in backend\.env, then re-run start.ps1'
  }
  Fail 'backend\.env is missing and .env.example was not found.'
}
Write-Ok $envFile

# ---------------------------------------------------------------- 4. port
Write-Step "Checking port $Port"
function Get-Listener([int]$p) {
  try { return Get-NetTCPConnection -LocalPort $p -State Listen -ErrorAction Stop | Select-Object -First 1 }
  catch { return $null }
}
function Test-OurUvicorn([int]$procId) {
  try {
    $cl = (Get-CimInstance Win32_Process -Filter "ProcessId=$procId").CommandLine
    return ($cl -and $cl -match 'uvicorn' -and $cl -match 'app\.main:app')
  } catch { return $false }
}

$listener = Get-Listener $Port
if ($listener) {
  $owner = $listener.OwningProcess
  if (Test-OurUvicorn $owner) {
    if (-not $Force) {
      Write-Ok "Already running: PID $owner"
      Write-Host "      API  : http://${BindHost}:$Port/api/v1/health"
      Write-Host "      Board: http://${BindHost}:$Port/industry.html"
      exit 0
    }
    Write-Warn2 "-Force: stopping the previous instance (PID $owner)"
    Stop-Process -Id $owner -Force -ErrorAction SilentlyContinue
    Start-Sleep -Seconds 2
  } else {
    Fail "Port $Port is held by PID $owner, which is not this project's uvicorn. Use -Port <other> or stop that process."
  }
}

# ---------------------------------------------------------------- 5. start
New-Item -ItemType Directory -Force -Path $runDir | Out-Null
Set-Location -LiteralPath $backend

$uvArgs = @('-m', 'uvicorn', 'app.main:app', '--host', $BindHost, '--port', "$Port")

if ($Foreground) {
  Write-Step "Starting uvicorn in the foreground on http://${BindHost}:$Port"
  & $py @uvArgs
  exit $LASTEXITCODE
}

# Background launch goes through scripts\serve.py (subprocess + DETACHED_PROCESS):
#   - Start-Process is unusable in restricted shells (shell association lookup fails);
#   - a child inheriting this shell's stdout makes callers block until uvicorn exits;
#   - PS 5.1 cannot run data-received event handlers without a runspace.
# serve.py writes uvicorn's stdio to .run\api.stdout.log and prints the PID.
Write-Step "Starting uvicorn on http://${BindHost}:$Port"
$servePy = Join-Path $PSScriptRoot 'serve.py'
$psi = New-Object System.Diagnostics.ProcessStartInfo
$psi.FileName = $py
$psi.Arguments = '"' + $servePy + '" --host ' + $BindHost + ' --port ' + $Port
$psi.WorkingDirectory = $repo
$psi.UseShellExecute = $false
$psi.CreateNoWindow = $true
$psi.RedirectStandardOutput = $true
$psi.RedirectStandardError = $true
$launcher = [System.Diagnostics.Process]::Start($psi)
$launchOut = $launcher.StandardOutput.ReadToEnd()
$launchErr = $launcher.StandardError.ReadToEnd()
[void]$launcher.WaitForExit(30000)
if ($launcher.ExitCode -ne 0) {
  if ($launchErr) { Write-Host $launchErr -ForegroundColor DarkGray }
  Fail 'serve.py could not start uvicorn (see backend\.run\api.stdout.log).'
}
$uvicornPid = $null
foreach ($line in ($launchOut -split "`r?`n")) {
  if ($line.Trim() -match '^\d+$') { $uvicornPid = [int]$line.Trim() }
}
if (-not $uvicornPid) { Fail "serve.py did not report a PID. Output: $launchOut" }
Set-Content -LiteralPath $pidFile -Value $uvicornPid -Encoding ASCII
Write-Ok "PID $uvicornPid (log: backend\.run\api.stdout.log)"

# ---------------------------------------------------------------- 6. wait for health
$health = "http://${BindHost}:$Port/api/v1/health"
$ready = $false
for ($i = 0; $i -lt 40; $i++) {
  Start-Sleep -Milliseconds 500
  if (-not (Get-Process -Id $uvicornPid -ErrorAction SilentlyContinue)) {
    Write-Warn2 'uvicorn exited. Last log lines:'
    $logPath = Join-Path $runDir 'api.stdout.log'
    if (Test-Path -LiteralPath $logPath) { Get-Content -LiteralPath $logPath -Tail 25 | Write-Host }
    Remove-Item -LiteralPath $pidFile -ErrorAction SilentlyContinue
    Fail 'Backend failed to start.'
  }
  try {
    $r = Invoke-WebRequest -Uri $health -UseBasicParsing -TimeoutSec 3
    if ($r.StatusCode -eq 200) { $ready = $true; break }
  } catch { }
}

if (-not $ready) {
  Stop-Process -Id $uvicornPid -Force -ErrorAction SilentlyContinue
  Remove-Item -LiteralPath $pidFile -ErrorAction SilentlyContinue
  Fail "Backend did not answer $health within 20s (uvicorn was stopped again)."
}

$body = $null
try { $body = Invoke-RestMethod -Uri $health -TimeoutSec 5 } catch { }
$db = if ($body -and $body.data) { $body.data.db } else { 'unknown' }

# /api/v1/health 按设计在 DB 不可达时仍返回 HTTP 200 + db=down（docs/03），
# 所以这里不能再把「进程活着」当成「服务可用」——否则 .env 密码为空时脚本会
# 打印 "Healthy (db=down)" 并退出 0，使用者以为启动成功了。
if ($db -ne 'up') {
  Write-Warn2 "API process is up, but the database is DOWN (db=$db)."
  Write-Warn2 'Check DB_HOST / DB_USER / DB_PASSWORD in backend\.env.'
  Write-Warn2 'MySQL error 1045 = password empty or wrong; 2003 = mysqld not reachable.'
  $apiLog = Join-Path $backend 'api_run.log'
  if (Test-Path -LiteralPath $apiLog) {
    Write-Host '  --- api_run.log (tail) ---' -ForegroundColor DarkGray
    Get-Content -LiteralPath $apiLog -Tail 12 | Write-Host
  }
  Write-Host ''
  Write-Host "  The API is still listening on http://${BindHost}:$Port (stop it with scripts\stop.ps1)." -ForegroundColor Yellow
  exit 1
}

Write-Ok "Healthy (db=$db)"
Write-Host ''
Write-Host '  Dashboard : ' -NoNewline; Write-Host "http://${BindHost}:$Port/industry.html" -ForegroundColor Green
Write-Host '  Fund flow : ' -NoNewline; Write-Host "http://${BindHost}:$Port/fundflow.html" -ForegroundColor Green
Write-Host '  Admin     : ' -NoNewline; Write-Host "http://${BindHost}:$Port/admin.html"     -ForegroundColor Green
Write-Host '  API docs  : ' -NoNewline; Write-Host "http://${BindHost}:$Port/docs"           -ForegroundColor Green
Write-Host ''
Write-Host '  Dark theme: append ?theme=dark to any page URL'
Write-Host '  Stop with : powershell -File scripts\stop.ps1'
exit 0
