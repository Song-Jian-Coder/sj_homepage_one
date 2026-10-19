#Requires -Version 5.1
<#
  lib-python.ps1 - interpreter discovery shared by start / stop / bootstrap / check.

  Dot-sourced, not executed:
    . (Join-Path $PSScriptRoot 'lib-python.ps1')

  Why this exists: the repo lives under a path containing "[AI工作区]", and several
  environments (scheduled tasks, restricted shells) expose an empty $env:LOCALAPPDATA
  while the interpreter is still installed. Naive `Join-Path $env:LOCALAPPDATA ...`
  throws "parameter Path is null", and `Get-Command python` may resolve to the
  0-byte WindowsApps alias. So: build candidates defensively, skip empties, verify
  each one by actually importing sys.
#>

function Test-PythonExe([string]$exe) {
  if ([string]::IsNullOrWhiteSpace($exe)) { return $false }
  try { if (-not (Test-Path -LiteralPath $exe)) { return $false } } catch { return $false }
  try {
    & $exe -c "import sys" 2>$null | Out-Null
    return ($LASTEXITCODE -eq 0)
  } catch { return $false }
}

function Get-PythonCandidates {
  param([string]$Explicit, [string]$RepoRoot, [string]$BackendRoot)

  $list = New-Object System.Collections.ArrayList
  if ($Explicit) { [void]$list.Add($Explicit) }
  if ($BackendRoot) { [void]$list.Add((Join-Path $BackendRoot '.venv\Scripts\python.exe')) }
  if ($RepoRoot)    { [void]$list.Add((Join-Path $RepoRoot    '.venv\Scripts\python.exe')) }

  # LocalAppData\Programs\Python\Python3xx (per-user install, the common case)
  $localApp = $env:LOCALAPPDATA
  if (-not $localApp) { try { $localApp = [Environment]::GetFolderPath('LocalApplicationData') } catch { } }
  if ($localApp) { [void]$list.Add((Join-Path $localApp 'Programs\Python\Python313\python.exe')) }

  # scan <user>\AppData\Local\Programs\Python\Python3* - survives an empty LOCALAPPDATA
  $userDirs = New-Object System.Collections.ArrayList
  foreach ($u in @($env:USERPROFILE, ([Environment]::GetFolderPath('UserProfile')))) {
    if ($u) { [void]$userDirs.Add($u) }
  }
  if ($env:HOMEDRIVE -and $env:HOMEPATH) { [void]$userDirs.Add($env:HOMEDRIVE + $env:HOMEPATH) }
  foreach ($u in ($userDirs | Select-Object -Unique)) {
    $pattern = Join-Path $u 'AppData\Local\Programs\Python\Python3*\python.exe'
    try {
      Get-ChildItem -Path $pattern -ErrorAction Stop |
        Sort-Object FullName -Descending |
        ForEach-Object { [void]$list.Add($_.FullName) }
    } catch { }
  }

  # machine-wide + py launcher
  foreach ($p in @('C:\Python313\python.exe', 'C:\Python312\python.exe', 'C:\Python311\python.exe')) {
    [void]$list.Add($p)
  }
  $pyLauncher = Join-Path $env:SystemRoot 'py.exe'
  if ($env:SystemRoot) { [void]$list.Add($pyLauncher) }

  # PATH
  $cmd = Get-Command python -All -ErrorAction SilentlyContinue
  if ($cmd) { foreach ($c in @($cmd)) { if ($c -and $c.Source) { [void]$list.Add([string]$c.Source) } } }

  return @($list | Where-Object { $_ } | Select-Object -Unique)
}

function Find-PythonExe {
  param([string]$Explicit, [string]$RepoRoot, [string]$BackendRoot)

  $candidates = Get-PythonCandidates -Explicit $Explicit -RepoRoot $RepoRoot -BackendRoot $BackendRoot
  foreach ($c in $candidates) {
    if (Test-PythonExe $c) { return $c }
  }
  Write-Host '  Candidate interpreters tried:' -ForegroundColor DarkGray
  foreach ($c in $candidates) { Write-Host "    $c" -ForegroundColor DarkGray }
  return $null
}

function Find-NodeExe([string]$Explicit) {
  if ($Explicit) {
    try { if (Test-Path -LiteralPath $Explicit) { return $Explicit } } catch { }
  }
  $cmd = Get-Command node -ErrorAction SilentlyContinue
  if ($cmd -and $cmd.Source) { return [string]$cmd.Source }
  foreach ($p in @('C:\Program Files\nodejs\node.exe', 'C:\Program Files (x86)\nodejs\node.exe')) {
    if (Test-Path -LiteralPath $p) { return $p }
  }
  return $null
}
