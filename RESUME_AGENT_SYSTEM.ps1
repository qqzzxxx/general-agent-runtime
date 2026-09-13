param(
    [switch]$Json,
    [switch]$NoStart
)
$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $MyInvocation.MyCommand.Path
$ArgsList = @((Join-Path $Root "scripts\supervisor_control.py"), "--root", $Root, "resume")
if ($Json) { $ArgsList += "--json" }
# Compatibility option: only an already verified running owner can satisfy it.
if ($NoStart) { $ArgsList += "--no-start" }
if ($Json) {
    $Result = (& python @ArgsList | Out-String).Trim()
    $ExitCode = $LASTEXITCODE
    $Document = $Result | ConvertFrom-Json
    if ($ExitCode -eq 0) {
        $Document = [ordered]@{ ok = $true; resume = $Document; startup = $Document.startup }
    }
    [Console]::Out.WriteLine(($Document | ConvertTo-Json -Depth 20 -Compress))
    exit $ExitCode
}
& python @ArgsList
exit $LASTEXITCODE
