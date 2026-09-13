# G5A: General Agent Runtime V1 — unified project bootstrap entry point.
# Mechanical initialization only: validate -> create -> activate. No research judgment,
# no task creation, no GLM/Codex invocation, no Scheduled Automation changes.
param(
    [Parameter(Mandatory = $true)][string]$ProjectId,
    [Parameter(Mandatory = $true)][string]$ProjectType,
    [string]$Goal,
    [string]$GoalFile,
    [string]$RuntimeRoot
)

$ErrorActionPreference = "Stop"
if (-not $RuntimeRoot) { $RuntimeRoot = $PSScriptRoot }  # default: this installation

$script = Join-Path $RuntimeRoot "scripts\start_project.py"
if (!(Test-Path $script)) { throw "start_project.py not found: $script" }

$py = "python"
try { $null = Get-Command $py -ErrorAction Stop } catch { throw "python not found on PATH" }

$args = @($script, "--root", $RuntimeRoot, "--project-id", $ProjectId, "--project-type", $ProjectType)
if ($Goal -and $GoalFile) { throw "Provide either -Goal or -GoalFile, not both." }
if ($Goal) { $args += @("--goal", $Goal) }
if ($GoalFile) { $args += @("--goal-file", $GoalFile) }

& $py @args
exit $LASTEXITCODE
