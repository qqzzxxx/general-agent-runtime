param(
    [Parameter(ParameterSetName="Text", Mandatory=$true)][string]$Text,
    [Parameter(ParameterSetName="File", Mandatory=$true)][string]$InstructionFile,
    [ValidateSet("STEER", "AUDIT")][string]$Mode = "STEER",
    [ValidateRange(0, [int]::MaxValue)][int]$TargetMessageId,
    [switch]$InterruptCurrentTask,
    [switch]$Json
)
$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $MyInvocation.MyCommand.Path
$ArgsList = @((Join-Path $Root "scripts\supervisor_control.py"), "--root", $Root, "intervene", "--mode", $Mode)
$TempInstruction = $null
try {
    if ($PSCmdlet.ParameterSetName -eq "Text") {
        # Windows PowerShell 5's native argument marshalling removes embedded
        # quotes in some argv forms. A UTF-8 no-BOM file is byte-stable in PS5/7.
        $TempInstruction = [System.IO.Path]::GetTempFileName()
        $Utf8NoBom = New-Object System.Text.UTF8Encoding($false, $true)
        [System.IO.File]::WriteAllText($TempInstruction, $Text, $Utf8NoBom)
        $ArgsList += @("--instruction-file", $TempInstruction)
    } else {
        $ResolvedInstruction = (Resolve-Path -LiteralPath $InstructionFile).Path
        $ArgsList += @("--instruction-file", $ResolvedInstruction)
    }
    if ($PSBoundParameters.ContainsKey("TargetMessageId")) { $ArgsList += @("--target-message-id", [string]$TargetMessageId) }
    if ($InterruptCurrentTask) { $ArgsList += "--interrupt-current-task" }
    if ($Json) { $ArgsList += "--json" }
    & python @ArgsList
    $ExitCode = $LASTEXITCODE
} finally {
    if ($TempInstruction) { Remove-Item -LiteralPath $TempInstruction -Force -ErrorAction SilentlyContinue }
}
if ($ExitCode -ne 0) { exit $ExitCode }
