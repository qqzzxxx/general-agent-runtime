param(
    [switch]$Json,
    [switch]$NoStart
)
$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $MyInvocation.MyCommand.Path
$ArgsList = @((Join-Path $Root "scripts\supervisor_control.py"), "--root", $Root, "resume")
if ($Json) {
    $ArgsList += "--json"
    $ResumeJson = (& python @ArgsList | Out-String).Trim()
    $ResumeExitCode = $LASTEXITCODE
} else {
    & python @ArgsList
    $ResumeExitCode = $LASTEXITCODE
}
if ($ResumeExitCode -ne 0) {
    if ($Json -and $ResumeJson) { [Console]::Out.WriteLine($ResumeJson) }
    exit $ResumeExitCode
}
$Startup = [ordered]@{
    requested = (-not $NoStart)
    status = $(if ($NoStart) { "NOT_REQUESTED" } else { "PENDING" })
    launched = $false
}
if (-not $NoStart) {
    $LockPath = Join-Path $Root "control\.orchestrator.lock"
    $LiveOwner = $false
    if (Test-Path -LiteralPath $LockPath) {
        try {
            $Lock = Get-Content -LiteralPath $LockPath -Raw | ConvertFrom-Json
            if ($Lock.pid) {
                $Owner = Get-Process -Id ([int]$Lock.pid) -ErrorAction SilentlyContinue
                if ($Owner) {
                    $LockTime = $null
                    try { $LockTime = [datetimeoffset]::Parse([string]$Lock.started_at).UtcDateTime } catch { $LockTime = $null }
                    if ($LockTime -and $Owner.StartTime.ToUniversalTime() -le $LockTime.AddSeconds(1)) {
                        $LiveOwner = $true
                    }
                }
            }
        } catch { $LiveOwner = $false }
    }
    if ($LiveOwner) {
        if ($Json) {
            $Startup.status = "EXISTING_OWNER"
            $Startup.launched = $false
            $Startup.owner_pid = [int]$Lock.pid
        } else {
            Write-Host "Runtime resumed; the existing Orchestrator will continue." -ForegroundColor Green
        }
    } else {
        if ($Json) {
            # A machine-control request must not inherit the caller's stdout/stderr
            # pipes or wait for the long-lived Orchestrator. Launch only this fixed
            # local script in a hidden PowerShell process and return one bounded JSON
            # response describing whether process creation succeeded.
            $StartScript = Join-Path $Root "START_AGENT_SYSTEM.ps1"
            try {
                if (-not (Test-Path -LiteralPath $StartScript -PathType Leaf)) {
                    throw "START_AGENT_SYSTEM.ps1 is missing"
                }
                $ShellPath = (Get-Process -Id $PID).Path
                $StartInfo = New-Object System.Diagnostics.ProcessStartInfo
                $StartInfo.FileName = $ShellPath
                $StartInfo.Arguments = ('-NoProfile -ExecutionPolicy Bypass -File "' +
                    $StartScript.Replace('"', '""') + '"')
                # Shell execution gives the child independent standard handles; the
                # JSON caller's capture pipe therefore closes when this wrapper exits.
                $StartInfo.UseShellExecute = $true
                $StartInfo.WindowStyle = [System.Diagnostics.ProcessWindowStyle]::Hidden
                $Child = [System.Diagnostics.Process]::Start($StartInfo)
                if (-not $Child) { throw "START_AGENT_SYSTEM process creation returned no process" }
                $ExitedPromptly = $Child.WaitForExit(300)
                if ($ExitedPromptly -and $Child.ExitCode -ne 0) {
                    $Startup.status = "FAILED"
                    $Startup.error = "START_AGENT_SYSTEM exited with code $($Child.ExitCode)"
                    $Startup.exit_code = [int]$Child.ExitCode
                } else {
                    $Startup.status = $(if ($ExitedPromptly) { "COMPLETED" } else { "LAUNCHED" })
                    $Startup.launched = $true
                    $Startup.pid = [int]$Child.Id
                }
            } catch {
                $Startup.status = "FAILED"
                $Startup.launched = $false
                $Startup.error = [string]$_.Exception.Message
            }
        } else {
            & (Join-Path $Root "START_AGENT_SYSTEM.ps1")
        }
    }
}
if ($Json) {
    $ResumeObject = $ResumeJson | ConvertFrom-Json
    $MachineResult = [ordered]@{
        ok = ($Startup.status -ne "FAILED")
        resume = $ResumeObject
        startup = $Startup
    }
    # The document contains only protocol tokens, timestamps and escaped Python
    # resume fields, keeping redirected Windows PowerShell output machine-readable.
    [Console]::Out.WriteLine(($MachineResult | ConvertTo-Json -Depth 12 -Compress))
    if ($Startup.status -eq "FAILED") { exit 1 }
}
