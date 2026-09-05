$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $Root

# Per-runtime-root instance isolation.
# This pre-check is advisory only: the authoritative enforcement is orchestrator.py
# acquire_lock() on control\.orchestrator.lock (exclusive create + dead-owner reclaim),
# which fails closed. Each runtime root owns its own lock, so independent runtime roots
# can run in parallel without blocking each other. A live owner recorded in THIS root's
# lock (pid alive, and the process started no later than the lock was written, guarding
# against pid reuse) blocks a second orchestrator for the same root.
$lockPath = Join-Path $Root "control\.orchestrator.lock"
if (Test-Path -LiteralPath $lockPath) {
    $lock = $null
    try { $lock = Get-Content -LiteralPath $lockPath -Raw | ConvertFrom-Json } catch { $lock = $null }
    $liveOwner = $false
    if ($lock -and $lock.pid) {
        $owner = Get-Process -Id ([int]$lock.pid) -ErrorAction SilentlyContinue
        if ($owner) {
            $lockTime = $null
            try {
                $lockTime = [datetimeoffset]::Parse([string]$lock.started_at).UtcDateTime
            } catch { $lockTime = $null }
            try { $ownerStart = $owner.StartTime.ToUniversalTime() } catch { $ownerStart = $null }
            # Fail closed: if the owner's start time cannot be proven to postdate the
            # lock (pid reuse), treat the lock as live.
            if (-not $lockTime -or -not $ownerStart -or $ownerStart -le $lockTime) {
                $liveOwner = $true
            }
        }
    }
    if ($liveOwner) {
        Write-Host "BLOCKED: another orchestrator for THIS runtime root is already running." -ForegroundColor Red
        Write-Host "Lock: $lockPath"
        if ($lock) { Write-Host "Recorded owner: pid=$($lock.pid) started_at=$($lock.started_at)" }
        Write-Host "Inspect control\.orchestrator.lock before removing it. Other runtime roots are unaffected." -ForegroundColor Yellow
        exit 10
    }
    Write-Host "Stale orchestrator lock found (owner not alive or pid reused); the orchestrator will reclaim it." -ForegroundColor Yellow
}

python scripts\preflight.py
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

python -m unittest scripts.test_orchestrator scripts.test_resume_human_review scripts.test_instance_isolation -q
if ($LASTEXITCODE -ne 0) {
    Write-Host "BLOCKED: mechanical regression tests failed." -ForegroundColor Red
    exit $LASTEXITCODE
}

Write-Host "Starting unattended dual-Agent orchestration..." -ForegroundColor Green
Write-Host "Codex will supervise; ZCode Desktop Scheduled Automation must already be enabled." -ForegroundColor Cyan
python orchestrator.py
exit $LASTEXITCODE
