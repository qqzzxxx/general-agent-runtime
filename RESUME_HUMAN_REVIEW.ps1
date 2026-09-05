# General Agent Runtime V1 — audited HUMAN_REVIEW resume entry point.
# This wrapper never starts the Orchestrator. Prepare and Apply are separate so the
# human can inspect the hash-bound receipt before committing the lifecycle change.
param(
    [Parameter(Mandatory = $true)]
    [ValidateSet("Prepare", "Apply")]
    [string]$Mode,
    [string]$ProjectId,
    [string]$DecisionFile,
    [Parameter(Mandatory = $true)]
    [string]$ReceiptFile,
    [string]$RuntimeRoot
)

$ErrorActionPreference = "Stop"
if (-not $RuntimeRoot) { $RuntimeRoot = $PSScriptRoot }

$script = Join-Path $RuntimeRoot "scripts\resume_human_review.py"
if (!(Test-Path -LiteralPath $script)) { throw "resume_human_review.py not found: $script" }

$py = "python"
try { $null = Get-Command $py -ErrorAction Stop } catch { throw "python not found on PATH" }

$args = @($script, "--root", $RuntimeRoot)
if ($Mode -eq "Prepare") {
    if (-not $ProjectId) { throw "-ProjectId is required in Prepare mode." }
    if (-not $DecisionFile) { throw "-DecisionFile is required in Prepare mode." }
    $args += @("prepare", "--project-id", $ProjectId,
               "--decision-file", $DecisionFile, "--receipt-out", $ReceiptFile)
} else {
    $args += @("apply", "--receipt", $ReceiptFile)
}

& $py @args
exit $LASTEXITCODE
