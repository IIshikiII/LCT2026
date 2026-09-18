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

    # Stop at a wall clock time, as "08:00". The next occurrence wins. This is
    # what an overnight run usually wants: work until morning, whatever the
    # limits did during the night. Empty disables it.
    [string] $StopAt = '',

    # Let Windows put the machine to sleep while the loop runs. Off by
    # default: a sleeping machine froze a loop through its own wake up time.
    [switch] $AllowMachineSleep,

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

function Initialize-KeepAwake {
    <#  Asks Windows not to sleep while the loop runs.

        A loop once went to wait out a limit until 04:12. Seventeen minutes
        later Windows hit its idle timeout and suspended the machine. The
        process froze mid wait, slept through its own wake up time and came
        back at 08:58 to find the wall clock cap long gone.

        SetThreadExecutionState is a request from this process, not a change
        to the machine settings. Windows drops it when the process ends. It
        stops an idle suspend only: a closed lid or a manual sleep still win. #>
    if ($AllowMachineSleep) { return $false }
    try {
        if (-not ('LoopPower.Native' -as [type])) {
            Add-Type -Namespace LoopPower -Name Native -MemberDefinition @'
[System.Runtime.InteropServices.DllImport("kernel32.dll", SetLastError = true)]
public static extern uint SetThreadExecutionState(uint esFlags);
'@
        }
        return (Set-KeepAwake)
    } catch {
        Write-Line ('Could not ask Windows to stay awake: ' + $_.Exception.Message) 'Yellow'
        return $false
    }
}

function Set-KeepAwake {
    <#  ES_CONTINUOUS | ES_SYSTEM_REQUIRED. The flag lives on the calling
        thread, so the wait loop renews it rather than setting it once. The
        display is free to switch off: only the system must stay up. #>
    if ($AllowMachineSleep) { return $false }
    if (-not ('LoopPower.Native' -as [type])) { return $false }
    try {
        $previous = [LoopPower.Native]::SetThreadExecutionState([uint32]2147483649)
        return ($previous -ne 0)
    } catch {
        return $false
    }
}

function Clear-KeepAwake {
    # ES_CONTINUOUS alone drops the requirement and lets the machine sleep.
    if (-not ('LoopPower.Native' -as [type])) { return }
    try { [void][LoopPower.Native]::SetThreadExecutionState([uint32]2147483648) } catch { }
}

function Get-SecondsLeft {
    <#  Seconds until the nearest wall clock deadline, whichever comes first.
        A run with neither deadline set gets a very large number. #>
    $left = [double]::MaxValue
    if ($HardStopHours -gt 0) {
        $left = ($script:startedAt.AddHours($HardStopHours) - (Get-Date)).TotalSeconds
    }
    if ($script:stopAtTime) {
        $byTime = ($script:stopAtTime - (Get-Date)).TotalSeconds
        if ($byTime -lt $left) { $left = $byTime }
    }
    return $left
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
    $apiStatus = 0
    $terminal = ''
    if ($stdout) {
        try {
            $parsed = $stdout | ConvertFrom-Json
            if ($parsed.total_cost_usd) { $cost = [double]$parsed.total_cost_usd }
            if ($parsed.num_turns) { $turns = [int]$parsed.num_turns }
            if ($null -ne $parsed.is_error) { $isError = [bool]$parsed.is_error }
            if ($parsed.result) { $summary = [string]$parsed.result }
            # The CLI reports a refusal by the service in fields, not in prose.
            # Reading the text alone missed a 429 and sent the loop into a
            # retry storm, so these two fields decide first.
            if ($parsed.api_error_status) { $apiStatus = [int]$parsed.api_error_status }
            if ($parsed.terminal_reason) { $terminal = [string]$parsed.terminal_reason }
        } catch { }
    }

    return [pscustomobject]@{
        ExitCode       = $proc.ExitCode
        Seconds        = [int]$elapsed
        Cost           = $cost
        Turns          = $turns
        IsError        = $isError
        Summary        = $summary
        ApiErrorStatus = $apiStatus
        TerminalReason = $terminal
        Raw            = ($stdout + "`n" + $stderr)
    }
}

function Test-LimitHit {
    <#  True when the service refused the call because a limit is spent.

        The status field decides first. Reading prose alone once missed the
        real message, "You've hit your session limit", and the loop burned
        nine iterations retrying in four minutes. #>
    param([psobject] $Run)

    if ($Run.ApiErrorStatus -eq 429) { return $true }

    $text = [string]$Run.Summary + "`n" + [string]$Run.Raw
    if (-not $text) { return $false }

    $patterns = @(
        "hit your .{0,20}limit",
        "(usage|rate|session|weekly) limit reached",
        "limit .{0,40}reset",
        "rate_limit_error",
        "429 Too Many Requests",
        "exceeded your usage",
        "upgrade to increase your usage"
    )
    foreach ($p in $patterns) {
        if ($text -match ('(?i)' + $p)) { return $true }
    }
    return $false
}

function Get-ResetWait {
    <#  Seconds to wait before trying again after a limit.

        Three sources of a reset time, best first:
          1. an epoch stamp, as in "limit reached|1750000000";
          2. a wall clock, as in "resets 7:10pm (Europe/Moscow)";
          3. nothing, so a growing pause.

        The clock form is read in local time. A different timezone in the
        message would shift the answer, and that is safe: waking too early
        only costs one more refusal and another wait. #>
    param([psobject] $Run, [int] $Attempt)

    $text = [string]$Run.Summary + "`n" + [string]$Run.Raw
    $maxWait = 7 * 3600

    $epoch = [regex]::Match($text, 'limit reached\|(?<epoch>\d{9,13})')
    if ($epoch.Success) {
        $raw = [double]$epoch.Groups['epoch'].Value
        if ($raw -gt 1e11) { $raw = $raw / 1000 }
        $reset = [System.DateTimeOffset]::FromUnixTimeSeconds([long]$raw).LocalDateTime
        $wait = ($reset - (Get-Date)).TotalSeconds + 120
        if ($wait -lt 60) { $wait = 60 }
        if ($wait -gt $maxWait) { $wait = $maxWait }
        Write-Line ('Limit resets at ' + $reset.ToString('HH:mm:ss')) 'Yellow'
        return [int]$wait
    }

    $clock = [regex]::Match($text, '(?i)resets?\s+(?:at\s+)?(?<h>\d{1,2})(?::(?<min>\d{2}))?\s*(?<ap>am|pm)?')
    if ($clock.Success) {
        $h = [int]$clock.Groups['h'].Value
        $min = 0
        if ($clock.Groups['min'].Success) { $min = [int]$clock.Groups['min'].Value }
        $ap = $clock.Groups['ap'].Value.ToLower()
        if ($ap -eq 'pm' -and $h -lt 12) { $h = $h + 12 }
        if ($ap -eq 'am' -and $h -eq 12) { $h = 0 }
        if ($h -le 23 -and $min -le 59) {
            $now = Get-Date
            $target = Get-Date -Hour $h -Minute $min -Second 0
            if ($target -le $now) { $target = $target.AddDays(1) }
            $wait = ($target - $now).TotalSeconds + 120
            if ($wait -gt $maxWait) { $wait = $maxWait }
            Write-Line ('Limit resets at ' + $target.ToString('HH:mm')) 'Yellow'
            return [int]$wait
        }
    }

    $minutes = @(10, 20, 40, 60, 60)[[math]::Min($Attempt, 4)]
    Write-Line ('Limit without a reset time. Waiting ' + $minutes + ' min.') 'Yellow'
    return $minutes * 60
}

function Wait-Until {
    <#  Waits, renewing the keep awake request, and reports a suspend.

        The request is renewed inside the loop because it belongs to a thread,
        and because a machine that was suspended by hand comes back without
        one. Overshooting the target by more than five minutes means the
        machine was asleep, and that is worth a line in the log: without it
        the gap looks like the driver hung. #>
    param([int] $Seconds, [string] $Phase, [string] $Task, [int] $Iteration)

    $until = (Get-Date).AddSeconds($Seconds)
    Write-Status -Iteration $Iteration -Phase $Phase -Task $Task
    Write-Line ('Sleeping until ' + $until.ToString('yyyy-MM-dd HH:mm:ss')) 'Yellow'

    while ((Get-Date) -lt $until) {
        if (Test-Path $stopPath) { return }
        [void](Set-KeepAwake)
        Start-Sleep -Seconds 30
    }

    $overshoot = ((Get-Date) - $until).TotalMinutes
    if ($overshoot -gt 5) {
        Write-Line ('Woke ' + [int]$overshoot + ' min late. The machine was suspended.') 'Yellow'
    }
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
        '| spent | $' + ([math]::Round($script:spentUsd, 2)).ToString([System.Globalization.CultureInfo]::InvariantCulture) + ' |',
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
$script:startSha = (Invoke-Git @('rev-parse', 'HEAD')).Trim()

# Wall clock deadline from -StopAt, the next time the clock shows that hour.
$script:stopAtTime = $null
if ($StopAt) {
    $m = [regex]::Match($StopAt, '^\s*(?<h>\d{1,2})(?::(?<min>\d{2}))?\s*$')
    if (-not $m.Success) { throw ('Cannot read -StopAt "' + $StopAt + '". Use HH:mm.') }
    $h = [int]$m.Groups['h'].Value
    $min = 0
    if ($m.Groups['min'].Success) { $min = [int]$m.Groups['min'].Value }
    if ($h -gt 23 -or $min -gt 59) { throw ('-StopAt "' + $StopAt + '" is not a time of day.') }
    $script:stopAtTime = Get-Date -Hour $h -Minute $min -Second 0
    if ($script:stopAtTime -le $script:startedAt) { $script:stopAtTime = $script:stopAtTime.AddDays(1) }
}
$limitAttempt = 0
$infraFails = 0
$maxInfraFails = 8
$taskAttempts = @{}
$iteration = 0

# Iteration numbers restart with every run, so a second run overwrote the logs
# of the first. The run stamp makes every file name unique.
$script:runStamp = $script:startedAt.ToString('yyyyMMdd-HHmmss')

$csvHeader = 'run,iteration,started,phase,task,model,seconds,cost_usd,turns,exit,gates'
if (Test-Path $runsCsv) {
    $firstLine = Get-Content -LiteralPath $runsCsv -Encoding ASCII -TotalCount 1
    if ($firstLine -ne $csvHeader) {
        $aside = Join-Path $logs ('runs-before-' + $script:runStamp + '.csv')
        Move-Item -LiteralPath $runsCsv -Destination $aside -Force
        Write-Line ('Old runs.csv has another shape. Moved to ' + (Split-Path -Leaf $aside) + '.') 'Yellow'
    }
}
if (-not (Test-Path $runsCsv)) {
    $csvHeader | Out-File -FilePath $runsCsv -Encoding ascii
}

Write-Line ('Loop start. Branch ' + $Branch + '. Budget ' + $Hours + ' active hours.') 'Cyan'
Write-Line ('Web tools: ' + $(if ($AllowWeb) { 'on' } else { 'off' })) 'Cyan'
if ($script:stopAtTime) {
    Write-Line ('Stop at ' + $script:stopAtTime.ToString('yyyy-MM-dd HH:mm')) 'Cyan'
}
if (Initialize-KeepAwake) {
    Write-Line 'Windows asked to stay awake. Check with: powercfg /requests' 'Cyan'
} elseif (-not $AllowMachineSleep) {
    Write-Line 'Could not hold the machine awake. An idle suspend will freeze the loop.' 'Yellow'
}

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
        Write-Line ('Wall clock cap of ' + $HardStopHours + ' h reached.') 'Cyan'; break
    }
    if ($script:stopAtTime -and (Get-Date) -ge $script:stopAtTime) {
        Write-Line ('Reached the stop time ' + $script:stopAtTime.ToString('HH:mm') + '.') 'Cyan'; break
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

    $stamp = 'run-' + $script:runStamp + '-iter-' + $iteration.ToString('000')
    $outPath = Join-Path $logs ($stamp + '.json')
    $errPath = Join-Path $logs ($stamp + '.err.txt')

    $run = Invoke-Claude -Prompt $prompt -Model $model -OutPath $outPath -ErrPath $errPath -Name ('loop-' + $stamp)

    if ($run.Summary -and $run.IsError) {
        Write-Line ('claude says: ' + ($run.Summary -replace "`r?`n", ' ')) 'Yellow'
    }

    # A spent limit is not a failed task. Wait it out and try the same task
    # again. Nothing was written, so the gates have nothing to check.
    if (Test-LimitHit -Run $run) {
        $limitAttempt++
        $wait = Get-ResetWait -Run $run -Attempt $limitAttempt

        # Sleeping past a deadline would end the loop asleep. Say so now, so
        # the last line of the log names the reason.
        $leftSec = Get-SecondsLeft
        if ($wait -ge $leftSec) {
            Write-Line ('The reset lands after the deadline, ' + [int]($leftSec / 60) + ' min from now. Exiting.') 'Red'
            break
        }

        Wait-Until -Seconds $wait -Phase 'waiting for limit reset' -Task $taskId -Iteration $iteration
        continue
    }
    $limitAttempt = 0

    if (Test-FatalError -Text $run.Raw) {
        Write-Line 'Authentication or balance problem. The loop cannot fix it. Exiting.' 'Red'
        break
    }

    # Someone stopped the call from outside: Ctrl+C in this console, or the
    # process killed. The work is gone but the task is not at fault, so the
    # attempt must not count. Usually the driver dies with the child and this
    # branch never runs. It matters when only the child was killed.
    if ($run.TerminalReason -like 'aborted*') {
        Write-Line 'The call was stopped from outside. Not counting it against the task.' 'Yellow'
        Write-Line 'To stop the loop cleanly, create the file loop/STOP instead.' 'Yellow'
        $script:activeSeconds += $run.Seconds
        $script:spentUsd += $run.Cost
        Start-Sleep -Seconds 5
        continue
    }

    # No money, no turns, non zero exit: the call never reached the model.
    # That is a fault of the machine or the service, not of the task, so it
    # must not count against the task and must not trigger the gates.
    if ($run.ExitCode -ne 0 -and $run.Cost -eq 0 -and $run.Turns -le 1) {
        $infraFails++
        Write-Line ('Call failed before any work. Attempt ' + $infraFails + ' of ' + $maxInfraFails + '.') 'Yellow'
        if ($infraFails -ge $maxInfraFails) {
            Write-Line 'Too many empty failures in a row. Exiting.' 'Red'
            break
        }
        $pause = @(120, 300, 600, 1200, 1800, 1800, 1800)[[math]::Min($infraFails - 1, 6)]
        Wait-Until -Seconds $pause -Phase 'waiting after an empty failure' -Task $taskId -Iteration $iteration
        continue
    }
    $infraFails = 0

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

    # A Russian locale writes 0,7759 for the cost, and the comma splits the
    # row into an extra column. Numbers in a CSV go through the invariant
    # culture, always.
    $invariant = [System.Globalization.CultureInfo]::InvariantCulture
    $row = @(
        $script:runStamp,
        $iteration,
        (Get-Date -Format 'yyyy-MM-ddTHH:mm:ss'),
        $phase,
        $taskId,
        $model,
        $run.Seconds,
        ([math]::Round($run.Cost, 4)).ToString($invariant),
        $run.Turns,
        $run.ExitCode,
        $(if ($gatesOk) { 'PASS' } else { 'FAIL' })
    ) -join ','
    Add-Content -LiteralPath $runsCsv -Value $row -Encoding ASCII

    Write-Status -Iteration $iteration -Phase $phase -Task $taskId

    if ($Once) { Write-Line 'Once mode. Exiting.' 'Cyan'; break }
    Start-Sleep -Seconds 5
}

Clear-KeepAwake

$made = '0'
if ($script:startSha) {
    $made = (Invoke-Git @('rev-list', '--count', ($script:startSha + '..HEAD'))).Trim()
}
$spent = ([math]::Round($script:spentUsd, 2)).ToString([System.Globalization.CultureInfo]::InvariantCulture)

Write-Line '' 'Cyan'
Write-Line ('Loop finished. Iterations ' + $iteration + '. Active ' + [int]($script:activeSeconds / 60) + ' min. Spent $' + $spent) 'Cyan'
Write-Line ('Wall clock ' + [int]((Get-Date) - $script:startedAt).TotalMinutes + ' min. Commits made: ' + $made) 'Cyan'
Write-Line 'Read loop/JOURNAL.md for what happened.' 'Cyan'
