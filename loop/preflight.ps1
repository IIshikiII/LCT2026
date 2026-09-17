<#
    preflight.ps1 - prepares and checks the environment for the loop.

    Run it once before run-loop.ps1. It is safe to run again: every step is
    idempotent. It fails loudly instead of letting the loop find the problem
    three hours in.

    ASCII only: Windows PowerShell 5.1 reads a BOM-less script as ANSI.
#>

[CmdletBinding()]
param(
    [string] $Branch = 'test_loop',
    [switch] $SkipGates,
    [switch] $SkipDb,

    # Do not touch .claude/agents. Useful when something else owns that folder.
    [switch] $SkipAgents
)

$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $PSScriptRoot
$loop = Join-Path $root 'loop'

$problems = New-Object System.Collections.ArrayList
$notes = New-Object System.Collections.ArrayList

function Check { param([string] $Name, [bool] $Ok, [string] $Detail = '')
    if ($Ok) {
        Write-Host ('  [ ok ] ' + $Name.PadRight(34) + ' ' + $Detail) -ForegroundColor Green
    } else {
        Write-Host ('  [fail] ' + $Name.PadRight(34) + ' ' + $Detail) -ForegroundColor Red
        [void]$problems.Add($Name + ' :: ' + $Detail)
    }
}

function Have { param([string] $Exe)
    $cmd = Get-Command $Exe -ErrorAction SilentlyContinue
    if ($cmd) { return $cmd.Source }
    return ''
}

function Get-QuotedArg {
    <#  Start-Process joins ArgumentList with spaces and quotes nothing. An
        argument that holds a space therefore arrives as two arguments, which
        turned `python -c "import duckdb"` into `-c import` plus `duckdb`.
        Quote here, once, so every caller can pass a plain array. #>
    param([string] $Value)
    if ([string]::IsNullOrEmpty($Value)) { return '""' }
    if ($Value -notmatch '[\s"]') { return $Value }
    return '"' + ($Value -replace '"', '\"') + '"'
}

function Invoke-Native {
    <#  Runs an external program and returns its exit code, never throwing.

        Windows PowerShell 5.1 wraps every stderr line of a native program in
        an ErrorRecord. With $ErrorActionPreference = 'Stop' that record is
        terminating, so a program that merely writes a warning kills the
        script. The guard hook did exactly that: it refused a command, wrote
        the reason to stderr, and preflight died on its own passing test.

        Start-Process keeps both streams in files, well away from the error
        stream, so nothing here can throw. #>
    param(
        [string]   $Exe,
        [string[]] $NativeArgs,
        [string]   $StdinText,
        [string]   $WorkDir = $root
    )

    $stem = Join-Path $env:TEMP ('loop-native-' + [guid]::NewGuid().ToString('N'))
    $outFile = $stem + '.out'
    $errFile = $stem + '.err'
    $inFile = $null

    $startArgs = @{
        FilePath               = $Exe
        WorkingDirectory       = $WorkDir
        NoNewWindow            = $true
        PassThru               = $true
        RedirectStandardOutput = $outFile
        RedirectStandardError  = $errFile
    }
    if ($NativeArgs -and $NativeArgs.Count -gt 0) {
        $startArgs['ArgumentList'] = @($NativeArgs | ForEach-Object { Get-QuotedArg $_ })
    }
    if ($PSBoundParameters.ContainsKey('StdinText')) {
        $inFile = $stem + '.in'
        # No byte order mark: some readers choke on one.
        [IO.File]::WriteAllText($inFile, $StdinText, (New-Object System.Text.UTF8Encoding($false)))
        $startArgs['RedirectStandardInput'] = $inFile
    }

    $code = -1
    $text = ''
    try {
        $proc = Start-Process @startArgs
        $null = $proc.Handle
        $proc.WaitForExit()
        $code = $proc.ExitCode
        if (Test-Path $outFile) {
            $text = (Get-Content -LiteralPath $outFile -Encoding UTF8 -Raw -ErrorAction SilentlyContinue)
        }
    } catch {
        $code = -1
    } finally {
        foreach ($f in @($outFile, $errFile, $inFile)) {
            if ($f) { Remove-Item -LiteralPath $f -Force -ErrorAction SilentlyContinue }
        }
    }
    if ($null -eq $text) { $text = '' }
    return [pscustomobject]@{ Code = $code; Out = $text }
}

Write-Host ''
Write-Host 'Preflight' -ForegroundColor Cyan
Write-Host '---------'

# --- tools -----------------------------------------------------------------

$claudeExe = Have 'claude'
Check 'claude on PATH' ([bool]$claudeExe) $claudeExe
if ($claudeExe) {
    $ver = (Invoke-Native -Exe $claudeExe -NativeArgs @('--version')).Out.Trim()
    Check 'claude version' ([bool]$ver) $ver
}

Check 'git on PATH'    ([bool](Have 'git'))    (Have 'git')
Check 'node on PATH'   ([bool](Have 'node'))   (Have 'node')
Check 'docker on PATH' ([bool](Have 'docker')) (Have 'docker')

# --- repository ------------------------------------------------------------

$gitExe = Have 'git'
$branchNow = (Invoke-Native -Exe $gitExe -NativeArgs @('rev-parse', '--abbrev-ref', 'HEAD')).Out.Trim()
Check 'git branch' ($branchNow -eq $Branch) ('on ' + $branchNow + ', want ' + $Branch)

$dirtyText = (Invoke-Native -Exe $gitExe -NativeArgs @('status', '--porcelain')).Out
$dirty = @($dirtyText -split "`r?`n" | Where-Object { $_ -match '\S' })
if ($dirty.Count -gt 0) {
    [void]$notes.Add('Working tree has ' + $dirty.Count + ' changed paths. The loop will commit them with its first task.')
}

# --- interpreters ----------------------------------------------------------

$rootPy = Join-Path $root '.venv\Scripts\python.exe'
$backPy = Join-Path $root 'backend\.venv\Scripts\python.exe'
Check 'research venv' (Test-Path $rootPy) $rootPy
Check 'backend venv'  (Test-Path $backPy) $backPy

function Test-Module { param([string] $Py, [string] $Module)
    if (-not (Test-Path $Py)) { return $false }
    # A missing module prints a traceback. Keep it out of the error stream.
    return ((Invoke-Native -Exe $Py -NativeArgs @('-c', ('import ' + $Module))).Code -eq 0)
}

if (Test-Path $rootPy) {
    foreach ($m in @('duckdb', 'pandas', 'pyarrow', 'sklearn', 'lightgbm', 'shap', 'pytest', 'ruff')) {
        Check ('research: ' + $m) (Test-Module $rootPy $m) ''
    }
}
if (Test-Path $backPy) {
    foreach ($m in @('fastapi', 'sqlalchemy', 'psycopg', 'pytest', 'mypy', 'ruff', 'joblib', 'sklearn')) {
        Check ('backend: ' + $m) (Test-Module $backPy $m) ''
    }
}

Check 'frontend node_modules' (Test-Path (Join-Path $root 'frontend\node_modules')) ''

# --- data ------------------------------------------------------------------

$parquet = Join-Path $root 'eda\out\events.parquet'
$sizeGb = 0
if (Test-Path $parquet) { $sizeGb = [math]::Round((Get-Item $parquet).Length / 1GB, 2) }
Check 'events.parquet' (Test-Path $parquet) ($sizeGb.ToString() + ' GB')
Check 'eda.duckdb' (Test-Path (Join-Path $root 'eda\out\eda.duckdb')) ''
Check 'channel reference' (Test-Path (Join-Path $root 'raw_task\dataset')) ''

# --- database ---------------------------------------------------------------

if (-not $SkipDb) {
    Write-Host '  starting postgres ...'
    $dockerExe = Have 'docker'
    $backendDir = Join-Path $root 'backend'
    if (-not $dockerExe) {
        Check 'postgres healthy' $false 'docker not found'
    } else {
        [void](Invoke-Native -Exe $dockerExe -NativeArgs @('compose', 'up', '-d', 'db') -WorkDir $backendDir)
        $healthy = $false
        for ($i = 0; $i -lt 40; $i++) {
            $state = (Invoke-Native -Exe $dockerExe `
                -NativeArgs @('compose', 'ps', '--format', '{{.Service}} {{.Status}}') `
                -WorkDir $backendDir).Out
            if ($state -match 'healthy') { $healthy = $true; break }
            Start-Sleep -Seconds 3
        }
        Check 'postgres healthy' $healthy ''
    }
}

# --- loop files -------------------------------------------------------------

New-Item -ItemType Directory -Path (Join-Path $loop 'logs') -Force | Out-Null

foreach ($f in @('GOAL.md', 'BACKLOG.md', 'prompts\iteration.md', 'prompts\repair.md', 'prompts\replan.md')) {
    Check ('loop/' + $f) (Test-Path (Join-Path $loop $f)) ''
}

$journal = Join-Path $loop 'JOURNAL.md'
if (-not (Test-Path $journal)) {
    "# Journal" | Out-File -FilePath $journal -Encoding utf8
}

New-Item -ItemType Directory -Path (Join-Path $loop 'scratch') -Force | Out-Null

# --- subagents --------------------------------------------------------------
# The definitions live in loop/agents so they travel with the loop. Claude Code
# only reads .claude/agents, so copy them there. Copying again is harmless.

$agentsSrc = Join-Path $loop 'agents'
$agentsDst = Join-Path $root '.claude\agents'
if ((Test-Path $agentsSrc) -and (-not $SkipAgents)) {
    New-Item -ItemType Directory -Path $agentsDst -Force | Out-Null
    $copied = 0
    foreach ($f in (Get-ChildItem -Path $agentsSrc -Filter '*.md')) {
        Copy-Item -LiteralPath $f.FullName -Destination $agentsDst -Force
        $copied++
    }
    Check 'subagents installed' ($copied -gt 0) ($copied.ToString() + ' into .claude/agents')
}

# --- session settings with the guard hook -----------------------------------

$guard = (Join-Path $loop 'guard.py')

# Build the JSON with ConvertTo-Json, never by hand. Hand written escaping put
# bare quotes inside a string value once, and Claude Code drops a settings file
# that fails to parse without saying a word. The hook was simply gone.
$hookCmd = '"' + $backPy + '" "' + $guard + '"'

$settingsObject = [ordered]@{
    includeCoAuthoredBy = $false
    hooks = [ordered]@{
        PreToolUse = @(
            [ordered]@{
                matcher = 'Bash|PowerShell'
                hooks = @(
                    [ordered]@{
                        type    = 'command'
                        command = $hookCmd
                        timeout = 20
                    }
                )
            }
        )
    }
    permissions = [ordered]@{
        deny = @('Bash(git push:*)', 'Bash(gh pr:*)', 'Bash(gh release:*)')
    }
}

$settingsPath = Join-Path $loop 'settings.loop.json'
($settingsObject | ConvertTo-Json -Depth 10) | Out-File -FilePath $settingsPath -Encoding ascii

$settingsValid = $false
try {
    $back = Get-Content -LiteralPath $settingsPath -Raw -Encoding UTF8 | ConvertFrom-Json
    $settingsValid = ($back.hooks.PreToolUse[0].hooks[0].command -eq $hookCmd)
} catch { }
Check 'settings.loop.json valid' $settingsValid $settingsPath

# --- prove the guard blocks -------------------------------------------------

if (Test-Path $backPy) {
    function Test-Guard { param([string] $Command)
        $payload = @{ tool_name = 'Bash'; tool_input = @{ command = $Command } } |
            ConvertTo-Json -Compress
        return (Invoke-Native -Exe $backPy -NativeArgs @($guard) -StdinText $payload).Code
    }

    # Exit code 2 means the hook refused the command.
    $blocked = @(
        'git push origin test_loop',
        'git reset --hard HEAD~1',
        'rm -rf eda/out',
        'docker compose down -v'
    )
    foreach ($c in $blocked) {
        $code = Test-Guard $c
        Check ('guard blocks: ' + $c) ($code -eq 2) ('exit ' + $code)
    }

    $allowed = @(
        'git commit -m hello',
        'docker compose up -d api',
        'npm run test'
    )
    foreach ($c in $allowed) {
        $code = Test-Guard $c
        Check ('guard allows: ' + $c) ($code -eq 0) ('exit ' + $code)
    }
}

# --- gates ------------------------------------------------------------------

if (-not $SkipGates) {
    Write-Host ''
    Write-Host 'Baseline gates' -ForegroundColor Cyan
    & powershell -ExecutionPolicy Bypass -NoProfile -File (Join-Path $loop 'gates.ps1') -Scope all | Out-Host
    $first = Get-Content -LiteralPath (Join-Path $loop 'GATES.md') -Encoding UTF8 -TotalCount 1
    Check 'baseline gates green' (-not ($first -match 'FAIL')) 'see loop/GATES.md'
}

# --- verdict ----------------------------------------------------------------

Write-Host ''
foreach ($n in $notes) { Write-Host ('  note: ' + $n) -ForegroundColor Yellow }

if ($problems.Count -eq 0) {
    Write-Host ''
    Write-Host 'Preflight passed. Start the loop with:' -ForegroundColor Green
    Write-Host '  powershell -ExecutionPolicy Bypass -File loop\run-loop.ps1 -Hours 8' -ForegroundColor Green
    exit 0
}

Write-Host ''
Write-Host ('Preflight found ' + $problems.Count + ' problems:') -ForegroundColor Red
foreach ($p in $problems) { Write-Host ('  - ' + $p) -ForegroundColor Red }
exit 1
