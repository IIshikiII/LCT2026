<#
    run-loop.ps1 - driver of the autonomous coding loop.

    One iteration is one fresh `claude -p` call. State lives in files, not in a
    session: loop/GOAL.md, loop/BACKLOG.md, loop/JOURNAL.md, loop/GATES.md.
    That is what makes the loop survive a rate limit, a crash and a full
    context window.

    The driver does four things the agent cannot do for itself:
      1. picks the next task and the model that fits it;
      2. runs the gates after every iteration and forces a repair iteration
         when they go red;
      3. waits out a usage limit and carries on;
      4. keeps the books: cost, tokens, elapsed active time.

    Stop it with Ctrl+C, or create the file loop/STOP.

    ASCII only: Windows PowerShell 5.1 reads a BOM-less script as ANSI.

    Examples:
      powershell -ExecutionPolicy Bypass -File loop/run-loop.ps1
      powershell -ExecutionPolicy Bypass -File loop/run-loop.ps1 -Hours 4 -Once
      powershell -ExecutionPolicy Bypass -File loop/run-loop.ps1 -MaxBudgetUsd 40
#>

[CmdletBinding()]
param(
    # Active work budget in hours. Waiting out a usage limit does not count.
    [double] $Hours = 8,

    # Wall clock cap. Protects against sleeping forever. 0 disables it.
    [double] $HardStopHours = 20,

    [string] $Branch = 'test_loop',

    [int] $MaxIterations = 400,

    # Stop when the spend passes this. 0 disables it.
    [double] $MaxBudgetUsd = 0,

    # Model for tasks with no tag in the backlog.
    [string] $DefaultModel = 'opus',

    # Run every gate, not only the ones the diff touched.
    [switch] $FullGates,

    # Let the agent reach the network. Off by default: an unattended agent with
    # permissions bypassed should not read text that tells it what to do.
    [switch] $AllowWeb,

    # One iteration, then exit. For a smoke test.
    [switch] $Once,

    # Print what would run and exit.
    [switch] $DryRun
)

$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $PSScriptRoot
$loop = Join-Path $root 'loop'
$logs = Join-Path $loop 'logs'
New-Item -ItemType Directory -Path $logs -Force | Out-Null

$backlogPath = Join-Path $loop 'BACKLOG.md'
$gatesPath   = Join-Path $loop 'GATES.md'
$statusPath  = Join-Path $loop 'STATUS.md'
$stopPath    = Join-Path $loop 'STOP'
$blockedPath = Join-Path $loop 'BLOCKED.txt'
$runsCsv     = Join-Path $logs 'runs.csv'

function Write-Line { param([string] $Text, [string] $Colour = 'Gray')
    Write-Host ((Get-Date -Format 'HH:mm:ss') + '  ' + $Text) -ForegroundColor $Colour
}

function Invoke-Git {
    <#  Runs git and returns its standard output as text, never throwing.

        Windows PowerShell 5.1 turns every stderr line of a native program
        into an ErrorRecord, and under $ErrorActionPreference = 'Stop' that
        record is terminating. A git warning would then kill a loop that has
        been running for hours. Start-Process keeps both streams in files,
        where they cannot reach the error stream. #>
    param([string[]] $GitArgs)

    $stem = Join-Path $env:TEMP ('loop-git-' + [guid]::NewGuid().ToString('N'))
    $outFile = $stem + '.out'
    $errFile = $stem + '.err'
    $text = ''
    try {
        $proc = Start-Process -FilePath 'git' -ArgumentList $GitArgs `
            -WorkingDirectory $root -NoNewWindow -PassThru `
            -RedirectStandardOutput $outFile -RedirectStandardError $errFile
        $null = $proc.Handle
        $proc.WaitForExit()
        if (Test-Path $outFile) {
            $text = (Get-Content -LiteralPath $outFile -Encoding UTF8 -Raw -ErrorAction SilentlyContinue)
        }
    } catch {
        $text = ''
    } finally {
        Remove-Item -LiteralPath $outFile, $errFile -Force -ErrorAction SilentlyContinue
    }
    if ($null -eq $text) { $text = '' }
    return $text
}

function Get-GitLines { param([string[]] $GitArgs)
    return @((Invoke-Git -GitArgs $GitArgs) -split "`r?`n" | Where-Object { $_ -match '\S' })
}

function Get-Blocked {
    if (-not (Test-Path $blockedPath)) { return @() }
    return @(Get-Content -LiteralPath $blockedPath -Encoding ASCII | Where-Object { $_ -match '\S' })
}

function Get-BacklogLines {
    <#  Backlog lines that are real task lines, not documentation.
        The header of BACKLOG.md shows the line format inside a fenced block.
        That example parses as a task and would send the loop after a task id
        that does not exist, so fenced blocks are skipped. #>
    if (-not (Test-Path $backlogPath)) { return @() }
    $out = New-Object System.Collections.ArrayList
    $inFence = $false
    foreach ($line in (Get-Content -LiteralPath $backlogPath -Encoding UTF8)) {
        if ($line -match '^\s*(```|~~~)') { $inFence = -not $inFence; continue }
        if (-not $inFence) { [void]$out.Add($line) }
    }
    return $out.ToArray()
}

function Get-NextTask {
    <#  Reads the backlog and returns the first open task.
        The line format is fixed:  - [ ] **T07** `opus` - text
        Only the id and the model tag cross the shell boundary. The Russian
        text stays inside the file, which Claude reads on its own. That keeps
        every command line pure ASCII and out of reach of code page trouble. #>
    $blocked = Get-Blocked
    $lines = Get-BacklogLines
    foreach ($line in $lines) {
        $m = [regex]::Match($line, '^\s*-\s*\[ \]\s*\*\*(?<id>[A-Za-z0-9]+)\*\*\s*(`(?<model>opus|sonnet|fable|haiku)`)?')
        if ($m.Success) {
            $id = $m.Groups['id'].Value
            if ($blocked -contains $id) { continue }
            $model = $m.Groups['model'].Value
            if (-not $model) { $model = $DefaultModel }
            return [pscustomobject]@{ Id = $id; Model = $model }
        }
    }
    return $null
}

function Test-GatesGreen {
    if (-not (Test-Path $gatesPath)) { return $true }
    $first = Get-Content -LiteralPath $gatesPath -Encoding UTF8 -TotalCount 1
    return -not ($first -match 'FAIL')
}

function Get-ChangedScope {
    param([string] $BaseSha)
    $changed = @()
    if ($BaseSha) {
        $changed += Get-GitLines @('diff', '--name-only', $BaseSha, 'HEAD')
    }
    $changed += @(Get-GitLines @('status', '--porcelain') |
        Where-Object { $_.Length -gt 3 } |
        ForEach-Object { $_.Substring(3) })
    $text = ($changed -join "`n")
    $front = ($text -match '(^|\n)"?frontend/')
    $back  = ($text -match '(^|\n)"?(backend|ml)/')
    if ($front -and $back) { return 'all' }
    if ($back)  { return 'backend' }
    if ($front) { return 'frontend' }
    return 'fast'
}

function Invoke-Claude {
    <#  Runs one iteration. Returns a record with the outcome.
        Start-Process with redirected files is the only reliable way to get an
        exit code and both streams out of a native program in PowerShell 5.1. #>
    param(
        [string] $Prompt,
        [string] $Model,
        [string] $OutPath,
        [string] $ErrPath,
        [string] $Name
    )

    $claudeArgs = New-Object System.Collections.ArrayList
    [void]$claudeArgs.Add('-p')
    [void]$claudeArgs.Add('"' + $Prompt + '"')
    [void]$claudeArgs.Add('--model');           [void]$claudeArgs.Add($Model)
    [void]$claudeArgs.Add('--permission-mode'); [void]$claudeArgs.Add('bypassPermissions')
    [void]$claudeArgs.Add('--output-format');   [void]$claudeArgs.Add('json')
    [void]$claudeArgs.Add('--settings');        [void]$claudeArgs.Add('"' + $settingsPath + '"')
    [void]$claudeArgs.Add('--name');            [void]$claudeArgs.Add($Name)

    if ($Model -eq 'opus') {
        [void]$claudeArgs.Add('--effort'); [void]$claudeArgs.Add('high')
        [void]$claudeArgs.Add('--fallback-model'); [void]$claudeArgs.Add('sonnet')
    } else {
        [void]$claudeArgs.Add('--effort'); [void]$claudeArgs.Add('medium')
    }

    if (-not $AllowWeb) {
        [void]$claudeArgs.Add('--disallowed-tools')
        [void]$claudeArgs.Add('WebFetch')
        [void]$claudeArgs.Add('WebSearch')
    }

    if ($MaxBudgetUsd -gt 0) {
        $left = [math]::Round($MaxBudgetUsd - $script:spentUsd, 2)
        if ($left -gt 0.5) {
            [void]$claudeArgs.Add('--max-budget-usd'); [void]$claudeArgs.Add($left)
        }
    }

    $started = Get-Date
    $proc = Start-Process -FilePath 'claude' -ArgumentList $claudeArgs.ToArray() `
        -WorkingDirectory $root -NoNewWindow -PassThru `
        -RedirectStandardOutput $OutPath -RedirectStandardError $ErrPath
    $null = $proc.Handle
    $proc.WaitForExit()
    $elapsed = ((Get-Date) - $started).TotalSeconds

    $stdout = ''
    if (Test-Path $OutPath) { $stdout = (Get-Content -LiteralPath $OutPath -Encoding UTF8 -Raw) }
    $stderr = ''
    if (Test-Path $ErrPath) { $stderr = (Get-Content -LiteralPath $ErrPath -Encoding UTF8 -Raw) }

    $cost = 0.0
    $turns = 0
    $isError = ($proc.ExitCode -ne 0)
    $summary = ''
    if ($stdout) {
        try {
            $parsed = $stdout | ConvertFrom-Json
            if ($parsed.total_cost_usd) { $cost = [double]$parsed.total_cost_usd }
            if ($parsed.num_turns) { $turns = [int]$parsed.num_turns }
            if ($null -ne $parsed.is_error) { $isError = [bool]$parsed.is_error }
            if ($parsed.result) { $summary = [string]$parsed.result }
        } catch { }
    }

    return [pscustomobject]@{
        ExitCode = $proc.ExitCode
        Seconds  = [int]$elapsed
        Cost     = $cost
        Turns    = $turns
        IsError  = $isError
        Summary  = $summary
        Raw      = ($stdout + "`n" + $stderr)
    }
}

function Get-LimitWait {
    <#  Looks for a usage limit in the output and returns how many seconds to
        wait. Returns 0 when the output shows no limit. The epoch form is the
        one the CLI prints when it knows the reset time; everything else falls
        back to a growing pause. #>
    param([string] $Text, [int] $Attempt)

    if (-not $Text) { return 0 }

    $epochMatch = [regex]::Match($Text, 'limit reached\|(?<epoch>\d{9,13})')
    if ($epochMatch.Success) {
        $raw = [double]$epochMatch.Groups['epoch'].Value
        if ($raw -gt 1e11) { $raw = $raw / 1000 }
        $reset = [System.DateTimeOffset]::FromUnixTimeSeconds([long]$raw).LocalDateTime
        $wait = ($reset - (Get-Date)).TotalSeconds + 120
        if ($wait -lt 60) { $wait = 60 }
        Write-Line ('Usage limit. Reset at ' + $reset.ToString('HH:mm:ss')) 'Yellow'
        return [int]$wait
    }

    $limitWords = @(
        'usage limit reached',
        'rate_limit_error',
        'Your limit will reset',
        'exceeded your usage',
        '429 Too Many Requests',
        'upgrade to increase your usage'
    )
    foreach ($word in $limitWords) {
        if ($Text -match [regex]::Escape($word)) {
            $minutes = @(10, 20, 40, 60, 60)[[math]::Min($Attempt, 4)]
            Write-Line ('Usage limit without a reset time. Waiting ' + $minutes + ' min.') 'Yellow'
            return $minutes * 60
        }
    }
    return 0
}

function Test-FatalError {
    param([string] $Text)
    if (-not $Text) { return $false }
    $fatal = @(
        'Invalid API key',
        'authentication_error',
        'Please run /login',
        'OAuth token has expired',
        'Credit balance is too low'
    )
    foreach ($word in $fatal) {
        if ($Text -match [regex]::Escape($word)) { return $true }
    }
    return $false
}

function Write-Status {
    param([int] $Iteration, [string] $Phase, [string] $Task)
    $activeMin = [int]($script:activeSeconds / 60)
    $budgetMin = [int]($Hours * 60)
    $lines = @(
        '# Loop status',
        '',
        'Updated ' + (Get-Date -Format 'yyyy-MM-dd HH:mm:ss'),
        '',
        '| Key | Value |',
        '|---|---|',
        '| iteration | ' + $Iteration + ' |',
        '| phase | ' + $Phase + ' |',
        '| task | ' + $Task + ' |',
        '| active | ' + $activeMin + ' of ' + $budgetMin + ' min |',
        '| started | ' + $script:startedAt.ToString('yyyy-MM-dd HH:mm:ss') + ' |',
        '| spent | $' + [math]::Round($script:spentUsd, 2) + ' |',
        '| gates | ' + $(if (Test-GatesGreen) { 'PASS' } else { 'FAIL' }) + ' |',
        '| branch | ' + $Branch + ' |',
        '',
        'Stop the loop by creating the file loop/STOP.'
    )
    $lines -join "`r`n" | Out-File -FilePath $statusPath -Encoding utf8
}

# --------------------------------------------------------------------------
# Preflight
# --------------------------------------------------------------------------

$settingsPath = Join-Path $loop 'settings.loop.json'
if (-not (Test-Path $settingsPath)) {
    throw 'loop/settings.loop.json is missing. Run loop/preflight.ps1 first.'
}

$current = (Invoke-Git @('rev-parse', '--abbrev-ref', 'HEAD')).Trim()
if ($current -ne $Branch) {
    throw ('Branch is ' + $current + ', expected ' + $Branch + '. Switch first.')
}

if (Test-Path $stopPath) { Remove-Item $stopPath -Force }

$script:startedAt = Get-Date
$script:activeSeconds = 0.0
$script:spentUsd = 0.0
$limitAttempt = 0
$taskAttempts = @{}
$iteration = 0

if (-not (Test-Path $runsCsv)) {
    'iteration,started,phase,task,model,seconds,cost_usd,turns,exit,gates' |
        Out-File -FilePath $runsCsv -Encoding ascii
}

Write-Line ('Loop start. Branch ' + $Branch + '. Budget ' + $Hours + ' active hours.') 'Cyan'
Write-Line ('Web tools: ' + $(if ($AllowWeb) { 'on' } else { 'off' })) 'Cyan'

if ($DryRun) {
    $next = Get-NextTask
    if ($next) { Write-Line ('Next task ' + $next.Id + ' on ' + $next.Model) 'Cyan' }
    else { Write-Line 'Backlog is empty. First iteration would replan.' 'Cyan' }
    exit 0
}

# --------------------------------------------------------------------------
# Main loop
# --------------------------------------------------------------------------

while ($true) {

    if (Test-Path $stopPath) { Write-Line 'STOP file found. Exiting.' 'Cyan'; break }
    if ($iteration -ge $MaxIterations) { Write-Line 'Iteration cap reached.' 'Cyan'; break }
    if ($script:activeSeconds -ge $Hours * 3600) { Write-Line 'Active time budget spent.' 'Cyan'; break }
    if ($HardStopHours -gt 0 -and ((Get-Date) - $script:startedAt).TotalHours -ge $HardStopHours) {
        Write-Line 'Wall clock cap reached.' 'Cyan'; break
    }
    if ($MaxBudgetUsd -gt 0 -and $script:spentUsd -ge $MaxBudgetUsd) {
        Write-Line 'Money budget spent.' 'Cyan'; break
    }

    $iteration++
    $baseSha = (Invoke-Git @('rev-parse', 'HEAD')).Trim()

    # Pick the phase. Red gates always win: the loop never stacks work on top
    # of a broken tree.
    if (-not (Test-GatesGreen)) {
        $phase = 'repair'
        $taskId = 'REPAIR'
        $model = 'sonnet'
        $prompt = 'Repair iteration of the autonomous loop. Follow @loop/prompts/repair.md exactly. Gates are red, see @loop/GATES.md.'
    } else {
        $next = Get-NextTask
        if ($next) {
            $phase = 'task'
            $taskId = $next.Id
            $model = $next.Model
            $prompt = 'Iteration ' + $iteration + ' of the autonomous loop. Follow @loop/prompts/iteration.md exactly. Your task id is ' + $taskId + ' in @loop/BACKLOG.md. Do that one task only.'
        } else {
            $phase = 'replan'
            $taskId = 'PLAN'
            $model = 'opus'
            $prompt = 'Planning iteration of the autonomous loop. Follow @loop/prompts/replan.md exactly.'
        }
    }

    Write-Line ('--- iteration ' + $iteration + ' | ' + $phase + ' | ' + $taskId + ' | ' + $model) 'White'
    Write-Status -Iteration $iteration -Phase $phase -Task $taskId

    $stamp = 'iter-' + $iteration.ToString('000')
    $outPath = Join-Path $logs ($stamp + '.json')
    $errPath = Join-Path $logs ($stamp + '.err.txt')

    $run = Invoke-Claude -Prompt $prompt -Model $model -OutPath $outPath -ErrPath $errPath -Name ('loop-' + $stamp)

    $wait = Get-LimitWait -Text $run.Raw -Attempt $limitAttempt
    if ($wait -gt 0) {
        $limitAttempt++
        $iteration--   # the iteration never happened
        Write-Status -Iteration $iteration -Phase 'waiting for limit reset' -Task $taskId
        $until = (Get-Date).AddSeconds($wait)
        Write-Line ('Sleeping until ' + $until.ToString('yyyy-MM-dd HH:mm:ss')) 'Yellow'
        while ((Get-Date) -lt $until) {
            if (Test-Path $stopPath) { break }
            Start-Sleep -Seconds 30
        }
        continue
    }
    $limitAttempt = 0

    if (Test-FatalError -Text $run.Raw) {
        Write-Line 'Authentication or balance problem. The loop cannot fix it. Exiting.' 'Red'
        break
    }

    $script:activeSeconds += $run.Seconds
    $script:spentUsd += $run.Cost
    Write-Line ('claude finished in ' + $run.Seconds + 's, $' + [math]::Round($run.Cost, 3) + ', ' + $run.Turns + ' turns, exit ' + $run.ExitCode)

    # Gates. Pick the narrow scope unless asked for everything, and run the
    # full set every fifth iteration so a slow drift cannot hide.
    # A repair iteration always gets the full set. A narrow scope could call
    # the tree green while the gate that actually failed never ran.
    $scope = 'all'
    if (-not $FullGates -and $phase -ne 'repair' -and ($iteration % 5 -ne 0)) {
        $scope = Get-ChangedScope -BaseSha $baseSha
    }
    & powershell -ExecutionPolicy Bypass -NoProfile -File (Join-Path $loop 'gates.ps1') -Scope $scope | Out-Host
    $gatesOk = Test-GatesGreen

    # Count attempts per task and park a task that keeps failing.
    if ($phase -eq 'task') {
        $done = $false
        foreach ($line in (Get-BacklogLines)) {
            if ($line -match ('^\s*-\s*\[[x\-]\]\s*\*\*' + [regex]::Escape($taskId) + '\*\*')) { $done = $true }
        }
        if ($done) {
            $taskAttempts.Remove($taskId)
        } else {
            if (-not $taskAttempts.ContainsKey($taskId)) { $taskAttempts[$taskId] = 0 }
            $taskAttempts[$taskId] = $taskAttempts[$taskId] + 1
            if ($taskAttempts[$taskId] -ge 3) {
                Add-Content -LiteralPath $blockedPath -Value $taskId -Encoding ASCII
                Write-Line ('Task ' + $taskId + ' failed three times. Parked in loop/BLOCKED.txt.') 'Yellow'
            }
        }
    }

    $row = @(
        $iteration,
        (Get-Date -Format 'yyyy-MM-ddTHH:mm:ss'),
        $phase,
        $taskId,
        $model,
        $run.Seconds,
        [math]::Round($run.Cost, 4),
        $run.Turns,
        $run.ExitCode,
        $(if ($gatesOk) { 'PASS' } else { 'FAIL' })
    ) -join ','
    Add-Content -LiteralPath $runsCsv -Value $row -Encoding ASCII

    Write-Status -Iteration $iteration -Phase $phase -Task $taskId

    if ($Once) { Write-Line 'Once mode. Exiting.' 'Cyan'; break }
    Start-Sleep -Seconds 5
}

Write-Line '' 'Cyan'
Write-Line ('Loop finished. Iterations ' + $iteration + '. Active ' + [int]($script:activeSeconds / 60) + ' min. Spent $' + [math]::Round($script:spentUsd, 2)) 'Cyan'
Write-Line ('Commits on ' + $Branch + ': ' + (Invoke-Git @('rev-list', '--count', 'HEAD')).Trim()) 'Cyan'
Write-Line 'Read loop/JOURNAL.md for what happened.' 'Cyan'
