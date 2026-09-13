param([switch]$Json)
$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $MyInvocation.MyCommand.Path
$ArgsList = @((Join-Path $Root "scripts\supervisor_control.py"), "--root", $Root, "status")
if ($Json) { $ArgsList += "--json" }
& python @ArgsList
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
