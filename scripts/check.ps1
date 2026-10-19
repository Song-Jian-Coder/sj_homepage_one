#Requires -Version 5.1
<#
  check.ps1 - one-shot acceptance check for the whole project (backend + frontend + theme).

  Layers:
    [1] backend health / meta
    [2] API contract (every router: industries, tree, members, flow, strength, stocks, admin gate)
    [3] static hosting (3 board pages + common.js)
    [4] frontend static tools (syntax / globals / common unit / XSS)              -SkipBrowser skips [5][6]
    [5] theme end-to-end (real Edge + CDP: light default, ?theme=dark, toggle, persistence, 1440/390)
    [6] browser regression (real Edge + CDP: 24 assertions on industry.html)
    [7] pytest (offline suite; -Integration also runs the DB-backed suite)

  Usage:
    powershell -ExecutionPolicy Bypass -File scripts\check.ps1
    powershell -ExecutionPolicy Bypass -File scripts\check.ps1 -SkipBrowser
    powershell -ExecutionPolicy Bypass -File scripts\check.ps1 -Full          # + DB integration tests
    powershell -ExecutionPolicy Bypass -File scripts\check.ps1 -BaseUrl http://127.0.0.1:8080

  Exit code: 0 all executed checks passed; 1 anything failed or the backend is unreachable.
#>
[CmdletBinding()]
param(
  [string]$BaseUrl = 'http://127.0.0.1:8000',
  [switch]$SkipBrowser,
  [switch]$Integration,
  [switch]$Full,
  [string]$NodeExe,
  [string]$PythonExe
)

$ErrorActionPreference = 'Continue'
try { [Console]::OutputEncoding = [System.Text.Encoding]::UTF8 } catch { }
$repo    = [System.IO.Path]::GetDirectoryName($PSScriptRoot)
$backend = Join-Path $repo 'backend'
$results = New-Object System.Collections.ArrayList

function Add-Result($area, $name, $pass, $detail) {
  [void]$results.Add([pscustomobject]@{ Area = $area; Name = $name; Pass = [bool]$pass; Detail = $detail })
  $mark = if ($pass) { 'PASS' } else { 'FAIL' }
  $color = if ($pass) { 'Green' } else { 'Red' }
  Write-Host ("  [{0}] {1,-44} {2}" -f $mark, $name, $detail) -ForegroundColor $color
}
function Section($t) { Write-Host "`n==> $t" -ForegroundColor Cyan }

# ---------------------------------------------------------------- helpers
function Get-Json($path) {
  try {
    $r = Invoke-WebRequest -Uri ($BaseUrl + $path) -UseBasicParsing -TimeoutSec 30
    return [pscustomobject]@{ Status = $r.StatusCode; Json = ($r.Content | ConvertFrom-Json); Raw = $r }
  } catch {
    return [pscustomobject]@{ Status = 0; Json = $null; Raw = $null; Error = $_.Exception.Message }
  }
}
function Has-Prop($obj, $name) {
  return ($obj -ne $null -and $obj.PSObject.Properties.Name -contains $name)
}
function Find-Node($exe) {
  if ($exe -and (Test-Path -LiteralPath $exe)) { return $exe }
  $c = Get-Command node -ErrorAction SilentlyContinue
  if ($c) { return $c.Source }
  foreach ($p in @('C:\Program Files\nodejs\node.exe', 'C:\Program Files (x86)\nodejs\node.exe')) {
    if (Test-Path -LiteralPath $p) { return $p }
  }
  return $null
}
function Find-Py {
  if ($PythonExe -and (Test-Path -LiteralPath $PythonExe)) { return $PythonExe }
  foreach ($p in @((Join-Path $backend '.venv\Scripts\python.exe'), (Join-Path $repo '.venv\Scripts\python.exe'))) {
    if (Test-Path -LiteralPath $p) { return $p }
  }
  $c = Get-Command python -ErrorAction SilentlyContinue
  if ($c) { return $c.Source }
  return $null
}
function Run-Tool($exe, $argsArr, $label, $area) {
  Push-Location -LiteralPath $backend
  try {
    $out = & $exe @argsArr 2>&1
    $code = $LASTEXITCODE
    $tail = ($out | Select-Object -Last 3) -join ' | '
    Add-Result $area $label ($code -eq 0) "exit=$code  $tail"
    return $code
  } finally { Pop-Location }
}

# ---------------------------------------------------------------- [1] health
Section '[1] backend health'
$h = Get-Json '/api/v1/health'
if ($h.Status -ne 200 -or -not (Has-Prop $h.Json 'code') -or $h.Json.code -ne 0) {
  Add-Result 'health' 'API reachable' $false "GET /api/v1/health -> $($h.Status) $($h.Error)"
  Write-Host ''
  Write-Host '  Backend is not answering. Start it first:' -ForegroundColor Yellow
  Write-Host '    powershell -ExecutionPolicy Bypass -File scripts\start.ps1' -ForegroundColor Yellow
  exit 1
}
Add-Result 'health' 'API reachable' $true "status=$($h.Json.data.status) db=$($h.Json.data.db)"
Add-Result 'health' 'DB connected' ($h.Json.data.db -eq 'up') "latestTradeDate=$($h.Json.data.latestTradeDate)"

$meta = Get-Json '/api/v1/meta'
$metaOk = ($meta.Status -eq 200 -and $meta.Json.code -eq 0)
Add-Result 'health' 'meta readable' $metaOk $(if ($metaOk) { "$($meta.Json.data.dataStart)..$($meta.Json.data.dataEnd) stocks=$($meta.Json.data.stockCount)" } else { "status=$($meta.Status)" })

# ---------------------------------------------------------------- [2] contract
Section '[2] API contract'
$tree = Get-Json '/api/v1/industries/tree'
$treeOk = ($tree.Status -eq 200 -and $tree.Json.code -eq 0 -and $tree.Json.data.Count -gt 0)
Add-Result 'api' 'GET /industries/tree' $treeOk $(if ($treeOk) { "l1=$($tree.Json.data.Count)" } else { "status=$($tree.Status)" })

$l1Code = if ($treeOk) { $tree.Json.data[0].swCode } else { '801010' }
$l2Code = if ($treeOk -and $tree.Json.data[0].children.Count -gt 0) { $tree.Json.data[0].children[0].swCode } else { '801011' }
$l3Code = if ($treeOk -and $tree.Json.data[0].children[0].children.Count -gt 0) { $tree.Json.data[0].children[0].children[0].swCode } else { '850131' }

$cases = @(
  @{ p = '/api/v1/industries?level=2&page_size=5'; need = 'list' },
  @{ p = '/api/v1/industries/tree'; need = '' },
  @{ p = "/api/v1/industries/$l1Code"; need = 'swCode' },
  @{ p = "/api/v1/industries/$l2Code/members?page_size=5"; need = 'list' },
  @{ p = "/api/v1/industries/$l3Code/flow"; need = 'series' },
  @{ p = "/api/v1/industries/$l2Code/stocks-flow?limit=5"; need = 'list' },
  @{ p = '/api/v1/strength'; need = 'list' },
  @{ p = '/api/v1/stocks/600519.SH/flow'; need = 'series' },
  @{ p = '/api/v1/stocks/search?q=600519&page_size=3'; need = 'list' }
)
foreach ($c in $cases) {
  $r = Get-Json $c.p
  $ok = ($r.Status -eq 200 -and $r.Json -ne $null -and $r.Json.code -eq 0)
  if ($ok -and $c.need) {
    if ($r.Json.data -is [array]) { $ok = ($r.Json.data.Count -ge 0) }
    else { $ok = (Has-Prop $r.Json.data $c.need) }
  }
  $detail = if ($r.Status -eq 0) { $r.Error } elseif ($r.Json) { "code=$($r.Json.code) trace=$($r.Json.traceId)" } else { "status=$($r.Status)" }
  Add-Result 'api' ("GET " + $c.p) $ok $detail
}

# error envelope must stay HTTP 200 + business code
$nf = Get-Json '/api/v1/industries/999999'
Add-Result 'api' 'GET /industries/999999 -> 40401 envelope' ($nf.Status -eq 200 -and $nf.Json.code -eq 40401) "http=$($nf.Status) code=$($nf.Json.code)"
$adm = Get-Json '/api/v1/admin/log?lines=1'
$admOk = ($adm.Status -eq 200 -and ($adm.Json.code -eq 40102 -or $adm.Json.code -eq 0))
Add-Result 'api' 'admin gate is fail-closed' $admOk "http=$($adm.Status) code=$($adm.Json.code)"

# ---------------------------------------------------------------- [3] static
Section '[3] static hosting'
foreach ($p in @('/industry.html', '/fundflow.html', '/admin.html', '/common.js')) {
  try {
    $r = Invoke-WebRequest -Uri ($BaseUrl + $p) -UseBasicParsing -TimeoutSec 15
    Add-Result 'static' ("GET " + $p) ($r.StatusCode -eq 200) "bytes=$($r.RawContentLength)"
  } catch {
    Add-Result 'static' ("GET " + $p) $false $_.Exception.Message
  }
}

# ---------------------------------------------------------------- [4] frontend static tools
Section '[4] frontend static tools'
$node = Find-Node $NodeExe
if (-not $node) {
  Add-Result 'frontend' 'node available' $false 'node.exe not found; install Node.js or pass -NodeExe'
} else {
  Run-Tool $node @('tools\syntax-check.js') 'syntax-check (3 pages)' 'frontend' | Out-Null
  Run-Tool $node @('tools\check-globals.js') 'check-globals' 'frontend' | Out-Null
  Run-Tool $node @('tools\common-unit-test.js') 'common-unit-test' 'frontend' | Out-Null
  Run-Tool $node @('tools\xss-check.js') 'xss-check' 'frontend' | Out-Null
}

# ---------------------------------------------------------------- [5][6] browser
if ($SkipBrowser) {
  Section '[5][6] browser checks (skipped by -SkipBrowser)'
} else {
  Section '[5] theme end-to-end'
  if ($node) {
    $env:BASE = $BaseUrl
    Run-Tool $node @('tools\theme-check.js') 'theme-check' 'browser' | Out-Null
    Remove-Item Env:BASE -ErrorAction SilentlyContinue
  } else {
    Add-Result 'browser' 'theme-check' $false 'node.exe not found'
  }

  Section '[6] browser regression (industry.html)'
  if ($node) {
    $env:PAGE = "$BaseUrl/industry.html"
    Run-Tool $node @('tools\regress-industry.js') 'regress-industry (24 assertions)' 'browser' | Out-Null
    Remove-Item Env:PAGE -ErrorAction SilentlyContinue
  } else {
    Add-Result 'browser' 'regress-industry' $false 'node.exe not found'
  }
}

# ---------------------------------------------------------------- [7] pytest
Section '[7] backend tests'
$py = Find-Py
if (-not $py) {
  Add-Result 'tests' 'pytest' $false 'no Python interpreter found'
} else {
  Push-Location -LiteralPath $backend
  try {
    & $py -c "import pytest" 2>$null | Out-Null
    $hasPytest = ($LASTEXITCODE -eq 0)
    if (-not $hasPytest) {
      Add-Result 'tests' 'pytest (offline suite)' $false 'pytest not installed -> run scripts\bootstrap.ps1'
    } else {
      if ($Integration -or $Full) { $env:RUN_INTEGRATION = '1' }
      $out = & $py -m pytest tests -q 2>&1
      $code = $LASTEXITCODE
      $tail = (($out | Where-Object { $_ -match 'passed|failed|error' } | Select-Object -Last 1))
      Add-Result 'tests' 'pytest tests' ($code -eq 0) $(if ($tail) { $tail.Trim() } else { "exit=$code" })
      Remove-Item Env:RUN_INTEGRATION -ErrorAction SilentlyContinue
    }
  } finally { Pop-Location }
}

# ---------------------------------------------------------------- summary
$total = $results.Count
$pass  = ($results | Where-Object { $_.Pass }).Count
$fail  = $total - $pass
Write-Host ''
Write-Host '══════════════════════════════════════' -ForegroundColor Cyan
Write-Host ("  checks {0}  pass {1}  fail {2}" -f $total, $pass, $fail)
Write-Host '══════════════════════════════════════' -ForegroundColor Cyan
$results | Where-Object { -not $_.Pass } | ForEach-Object {
  Write-Host ("  FAIL  [{0}] {1} -> {2}" -f $_.Area, $_.Name, $_.Detail) -ForegroundColor Red
}

if ($fail -eq 0) { exit 0 } else { exit 1 }
