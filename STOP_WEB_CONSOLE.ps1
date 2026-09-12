# General Agent Runtime v1.3 Web Console stopper (P1 skeleton).
# Stops ONLY the verified Web Console backend instance bound to this Runtime.
# Idempotent: stopping with no instance recorded succeeds. Refuses stale or
# foreign instance metadata instead of killing anything unverified, so the
# Runtime orchestrator and unrelated processes are never touched.
# Non-authoritative instance metadata lives under
# <this installation>\web_console_data\instance.json and is removed here after
# a successful stop (see docs/WEB_CONSOLE_DEVELOPMENT.md).
param(
    [string]$RuntimeRoot = "",
    [int]$WaitExitSeconds = 10
)
$ErrorActionPreference = "Stop"

$ConsoleRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$ServerScript = Join-Path $ConsoleRoot "scripts\web_console_server.py"
$DataDir = Join-Path $ConsoleRoot "web_console_data"
$MetadataFile = Join-Path $DataDir "instance.json"

function Write-Refuse([string]$Verdict, [string]$Reason) {
    Write-Host "WEB_CONSOLE_STOP_REFUSED verdict=$Verdict reason=$Reason"
    exit 3
}

if (-not (Test-Path $ServerScript)) { Write-Refuse "MISSING_SERVER" "server script missing: $ServerScript" }
$Python = "python"
try { $null = Get-Command $Python -ErrorAction Stop } catch { Write-Refuse "NO_PYTHON" "python not found on PATH" }

if ($RuntimeRoot) {
    try { $RuntimeRoot = (Resolve-Path $RuntimeRoot).Path } catch { Write-Refuse "BAD_RUNTIME_ROOT" "Runtime root does not exist: $RuntimeRoot" }
} else {
    $RuntimeRoot = $ConsoleRoot
}

$output = & $Python $ServerScript verify-instance --data-dir $DataDir --runtime-root $RuntimeRoot
if ($LASTEXITCODE -ne 0) { Write-Refuse "VERIFY_FAILED" "instance verification command failed" }
$verdict = ($output -join "`n") | ConvertFrom-Json

switch ($verdict.verdict) {
    "MISSING" {
        Write-Host "WEB_CONSOLE_NOT_RUNNING"
        exit 0
    }
    "STALE" {
        Remove-Item -LiteralPath $MetadataFile -Force -ErrorAction SilentlyContinue
        Write-Host "WEB_CONSOLE_STALE_METADATA_REMOVED pid=$($verdict.instance.pid)"
        exit 0
    }
    "VERIFIED" {
        $procId = $verdict.instance.pid
        Stop-Process -Id $procId -Force
        $deadline = (Get-Date).AddSeconds($WaitExitSeconds)
        while ((Get-Date) -lt $deadline) {
            if (-not (Get-Process -Id $procId -ErrorAction SilentlyContinue)) { break }
            Start-Sleep -Milliseconds 300
        }
        if (Get-Process -Id $procId -ErrorAction SilentlyContinue) {
            Write-Host "WEB_CONSOLE_STOP_INCOMPLETE pid=$procId"
            exit 6
        }
        Remove-Item -LiteralPath $MetadataFile -Force -ErrorAction SilentlyContinue
        Write-Host "WEB_CONSOLE_STOPPED pid=$procId port=$($verdict.instance.port)"
        exit 0
    }
    "INVALID" { Write-Refuse "INVALID" "$($verdict.reason); inspect web_console_data\instance.json manually" }
    "FOREIGN" { Write-Refuse "FOREIGN" "$($verdict.reason); refusing to stop another Runtime's instance" }
    "ALIVE_UNVERIFIED" { Write-Refuse "ALIVE_UNVERIFIED" "$($verdict.reason); refusing to kill an unverified process" }
    default { Write-Refuse "UNKNOWN" "unrecognized verdict: $($verdict.verdict)" }
}
