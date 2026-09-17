<#
    gates.ps1 - verification gates for the autonomous loop.

    Runs the project checks, writes a report to loop/GATES.md and exits 0 when
    everything is green, 1 otherwise. Both the driver and the agent call it.

    Every gate runs under a hard timeout. A hung gate would stall the whole
    loop, so a timeout counts as a failure and the process tree is killed.

    ASCII only: Windows PowerShell 5.1 reads a BOM-less script as ANSI.
#>

[CmdletBinding()]
param(
    # all | frontend | backend | ml | fast
    [string] $Scope = 'all',
    [int]    $TimeoutSec = 900,
    [string] $ReportPath
)

$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $PSScriptRoot
if (-not $ReportPath) { $ReportPath = Join-Path $root 'loop\GATES.md' }

# Force UTF-8 out of python and node so the report stays readable.
$env:PYTHONUTF8 = '1'
$env:PYTHONIOENCODING = 'utf-8'

$tmp = Join-Path $env:TEMP ('loop-gates-' + [guid]::NewGuid().ToString('N'))
New-Item -ItemType Directory -Path $tmp -Force | Out-Null

$results = New-Object System.Collections.ArrayList

function Invoke-Gate {
    param(
        [string]   $Name,
        [string]   $Exe,
        [string[]] $GateArgs,
        [string]   $WorkDir,
        [int]      $Timeout = $TimeoutSec
    )

    $outFile = Join-Path $tmp ($Name.Replace(' ', '_') + '.out')
    $errFile = Join-Path $tmp ($Name.Replace(' ', '_') + '.err')
    $started = Get-Date

    Write-Host ("  " + $Name.PadRight(28) + " ... ") -NoNewline

    $proc = Start-Process -FilePath $Exe -ArgumentList $GateArgs `
        -WorkingDirectory $WorkDir -NoNewWindow -PassThru `
        -RedirectStandardOutput $outFile -RedirectStandardError $errFile

    # Touching Handle caches it. Without that, ExitCode comes back empty on
    # a process started with -PassThru but without -Wait.
    $null = $proc.Handle

    $timedOut = $false
    if (-not $proc.WaitForExit($Timeout * 1000)) {
        $timedOut = $true
        try { taskkill /T /F /PID $proc.Id 2>$null | Out-Null } catch { }
        try { $proc.WaitForExit(5000) | Out-Null } catch { }
    }

    $elapsed = [int]((Get-Date) - $started).TotalSeconds
    $code = 1
    if (-not $timedOut) { $code = $proc.ExitCode }

    $text = ''
    foreach ($f in @($outFile, $errFile)) {
        if (Test-Path $f) {
            $part = Get-Content -LiteralPath $f -Encoding UTF8 -Raw -ErrorAction SilentlyContinue
            if ($part) { $text = $text + $part }
        }
    }

    $ok = (-not $timedOut) -and ($code -eq 0)
    if ($ok) { Write-Host ("PASS  " + $elapsed + "s") -ForegroundColor Green }
    elseif ($timedOut) { Write-Host ("TIMEOUT " + $elapsed + "s") -ForegroundColor Red }
    else { Write-Host ("FAIL  " + $elapsed + "s  exit=" + $code) -ForegroundColor Red }

    $lines = @()
    if ($text) { $lines = ($text -split "`r?`n") }
    $tail = $lines
    if ($lines.Count -gt 40) { $tail = $lines[($lines.Count - 40)..($lines.Count - 1)] }

    [void]$results.Add([pscustomobject]@{
        Name     = $Name
        Ok       = $ok
        TimedOut = $timedOut
        Code     = $code
        Seconds  = $elapsed
        Tail     = ($tail -join "`n")
    })
}

$frontend = Join-Path $root 'frontend'
$backend  = Join-Path $root 'backend'
$mlDir    = Join-Path $root 'ml'
$rootPy   = Join-Path $root '.venv\Scripts\python.exe'
$backPy   = Join-Path $backend '.venv\Scripts\python.exe'

$wantFrontend = ($Scope -eq 'all') -or ($Scope -eq 'frontend') -or ($Scope -eq 'fast')
$wantBackend  = ($Scope -eq 'all') -or ($Scope -eq 'backend')
$wantMl       = ($Scope -eq 'all') -or ($Scope -eq 'ml') -or ($Scope -eq 'backend')

Write-Host ("Gates scope=" + $Scope)

if ($wantFrontend -and (Test-Path (Join-Path $frontend 'node_modules'))) {
    Invoke-Gate -Name 'frontend typecheck' -Exe 'npm.cmd' -GateArgs @('run', 'typecheck') -WorkDir $frontend -Timeout 420
    Invoke-Gate -Name 'frontend lint'      -Exe 'npm.cmd' -GateArgs @('run', 'lint')      -WorkDir $frontend -Timeout 300
    if ($Scope -ne 'fast') {
        Invoke-Gate -Name 'frontend test'  -Exe 'npm.cmd' -GateArgs @('run', 'test')      -WorkDir $frontend -Timeout 900
    }
}

if ($wantBackend -and (Test-Path $backPy)) {
    Invoke-Gate -Name 'backend ruff'  -Exe $backPy -GateArgs @('-m', 'ruff', 'check', '.') -WorkDir $backend -Timeout 180
    Invoke-Gate -Name 'backend mypy'  -Exe $backPy -GateArgs @('-m', 'mypy', 'app')        -WorkDir $backend -Timeout 420
    Invoke-Gate -Name 'backend tests' -Exe $backPy -GateArgs @('-m', 'pytest', '-q')       -WorkDir $backend -Timeout 900
}

if ($wantMl -and (Test-Path $mlDir) -and (Test-Path $rootPy)) {
    $mlTests = @(Get-ChildItem -Path $mlDir -Recurse -Filter 'test_*.py' -ErrorAction SilentlyContinue)
    if ($mlTests.Count -gt 0) {
        Invoke-Gate -Name 'ml ruff'  -Exe $rootPy -GateArgs @('-m', 'ruff', 'check', 'ml') -WorkDir $root -Timeout 180
        Invoke-Gate -Name 'ml tests' -Exe $rootPy -GateArgs @('-m', 'pytest', 'ml', '-q')  -WorkDir $root -Timeout 900
    }
}

$failed = @($results | Where-Object { -not $_.Ok })
$green = ($failed.Count -eq 0)

$report = New-Object System.Collections.ArrayList
if ($green) { [void]$report.Add('# Gates: PASS') } else { [void]$report.Add('# Gates: FAIL') }
[void]$report.Add('')
[void]$report.Add('Generated ' + (Get-Date -Format 'yyyy-MM-dd HH:mm:ss') + ' scope=' + $Scope)
[void]$report.Add('')
[void]$report.Add('| Gate | Result | Seconds |')
[void]$report.Add('|---|---|---|')
foreach ($r in $results) {
    $verdict = 'PASS'
    if ($r.TimedOut) { $verdict = 'TIMEOUT' }
    elseif (-not $r.Ok) { $verdict = 'FAIL exit=' + $r.Code }
    [void]$report.Add('| ' + $r.Name + ' | ' + $verdict + ' | ' + $r.Seconds + ' |')
}
if ($results.Count -eq 0) {
    [void]$report.Add('| (nothing ran) | - | 0 |')
}
[void]$report.Add('')

if (-not $green) {
    [void]$report.Add('## Output of failing gates')
    [void]$report.Add('')
    foreach ($r in $failed) {
        [void]$report.Add('### ' + $r.Name)
        [void]$report.Add('')
        [void]$report.Add('```')
        [void]$report.Add($r.Tail)
        [void]$report.Add('```')
        [void]$report.Add('')
    }
    [void]$report.Add('Fix the cause, not the symptom. Do not delete or skip a test to')
    [void]$report.Add('turn a gate green. Reverting the last commit is a legal way out.')
}

$report -join "`r`n" | Out-File -FilePath $ReportPath -Encoding utf8
Remove-Item -Recurse -Force $tmp -ErrorAction SilentlyContinue

if ($green) {
    Write-Host 'Gates: PASS' -ForegroundColor Green
    exit 0
}
Write-Host ('Gates: FAIL (' + $failed.Count + ' of ' + $results.Count + ')') -ForegroundColor Red
exit 1
