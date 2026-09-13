[CmdletBinding()]
param(
    [string]$RuntimeRoot,
    [switch]$NoClipboard,
    [switch]$OutputPrompt
)

$ErrorActionPreference = "Stop"
$placeholder = "<RUNTIME_ROOT>"

try {
    if (-not $PSBoundParameters.ContainsKey("RuntimeRoot")) {
        $RuntimeRoot = $PSScriptRoot
    }

    if ([string]::IsNullOrWhiteSpace($RuntimeRoot)) {
        throw "Runtime Root must be a non-empty directory path."
    }

    try {
        $resolvedRoot = Resolve-Path -LiteralPath $RuntimeRoot -ErrorAction Stop
    }
    catch {
        throw "Runtime Root does not exist or cannot be resolved: $RuntimeRoot"
    }

    $rootItem = Get-Item -LiteralPath $resolvedRoot.ProviderPath -ErrorAction Stop
    if (-not $rootItem.PSIsContainer) {
        throw "Runtime Root is not a directory: $($rootItem.FullName)"
    }
    $absoluteRoot = $rootItem.FullName

    $promptPath = Join-Path $absoluteRoot "control\ZCODE_SCHEDULED_AUTOMATION_PROMPT.md"
    if (-not (Test-Path -LiteralPath $promptPath -PathType Leaf)) {
        throw "Canonical Executor prompt template is missing: $promptPath"
    }

    foreach ($requiredRelativePath in @("START_AGENT_SYSTEM.ps1", "orchestrator.py")) {
        $requiredPath = Join-Path $absoluteRoot $requiredRelativePath
        if (-not (Test-Path -LiteralPath $requiredPath -PathType Leaf)) {
            throw "Invalid Runtime Root; required file is missing: $requiredPath"
        }
    }

    # Read strictly as UTF-8 in memory. StreamReader detects and consumes a UTF-8 BOM
    # if one exists; the source template is never opened for writing.
    $utf8 = New-Object System.Text.UTF8Encoding($false, $true)
    $reader = New-Object System.IO.StreamReader($promptPath, $utf8, $true)
    try {
        $template = $reader.ReadToEnd()
    }
    finally {
        $reader.Dispose()
    }

    if (-not $template.Contains($placeholder)) {
        throw "Canonical Executor prompt template contains no literal $placeholder placeholder; it may have changed unexpectedly."
    }

    $renderedPrompt = $template.Replace($placeholder, $absoluteRoot)
    if ($renderedPrompt.Contains($placeholder)) {
        throw "Prompt rendering failed closed because a literal $placeholder placeholder remains."
    }

    $clipboardStatus = "Copied to clipboard."
    $pasteInstruction = "3. Paste the prompt from the clipboard."
    if ($NoClipboard) {
        $clipboardStatus = "Clipboard copy skipped (-NoClipboard)."
        $pasteInstruction = "3. Rerun without -NoClipboard or manually render and copy the prompt, then paste it."
    }
    else {
        $clipboardCommand = Get-Command Set-Clipboard -ErrorAction SilentlyContinue
        if ($null -eq $clipboardCommand) {
            throw "Set-Clipboard is unavailable. Rerun with -NoClipboard or manually render and copy the canonical prompt template."
        }
        try {
            & $clipboardCommand -Value $renderedPrompt -ErrorAction Stop
        }
        catch {
            throw "Clipboard write failed. Rerun with -NoClipboard or manually render and copy the canonical prompt template. $($_.Exception.Message)"
        }
    }

    Write-Output "ZCode Automation setup prepared."
    Write-Output ""
    Write-Output "Workspace:"
    Write-Output $absoluteRoot
    Write-Output ""
    Write-Output "Prompt:"
    Write-Output $clipboardStatus
    Write-Output ""
    Write-Output "Next:"
    Write-Output "1. Create or edit this Runtime's ZCode Scheduled Automation."
    Write-Output "2. Set Workspace to the Runtime Root shown above."
    Write-Output $pasteInstruction
    Write-Output "4. Select a compatible GLM-5.3 family variant, e.g. GLM-5.3-Flash."
    Write-Output "5. Keep the Automation paused until START_PROJECT and preflight are complete."
    Write-Output "6. Enable it immediately before START_AGENT_SYSTEM.ps1."

    if ($OutputPrompt) {
        Write-Output ""
        Write-Output "Rendered prompt:"
        Write-Output $renderedPrompt
    }
}
catch {
    [Console]::Error.WriteLine("PREPARE_ZCODE_AUTOMATION failed: $($_.Exception.Message)")
    exit 1
}
