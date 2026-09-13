param(
    [ValidateRange(1, [int]::MaxValue)][int]$Last,
    [switch]$Json
)
$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $MyInvocation.MyCommand.Path
$ArgsList = @((Join-Path $Root "scripts\supervisor_control.py"), "--root", $Root, "interventions")
if ($PSBoundParameters.ContainsKey("Last")) { $ArgsList += @("--last", [string]$Last) }
if ($Json) { $ArgsList += "--json" }
& python @ArgsList
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
