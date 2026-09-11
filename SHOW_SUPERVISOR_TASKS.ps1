param(
    [ValidateRange(1, [int]::MaxValue)][int]$Last,
    [ValidateRange(0, [int]::MaxValue)][int]$MessageId,
    [switch]$Json
)
$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $MyInvocation.MyCommand.Path
$ArgsList = @((Join-Path $Root "scripts\supervisor_control.py"), "--root", $Root, "tasks")
if ($PSBoundParameters.ContainsKey("Last")) { $ArgsList += @("--last", [string]$Last) }
if ($PSBoundParameters.ContainsKey("MessageId")) { $ArgsList += @("--message-id", [string]$MessageId) }
if ($Json) { $ArgsList += "--json" }
& python @ArgsList
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
