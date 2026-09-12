# General Agent Runtime v1.3 Web Console launcher (P1 skeleton).
# Starts at most one localhost-only backend for this Runtime, waits boundedly
# for health, and opens the default browser only after health succeeds.
#   -Foreground           diagnostic mode: run the server in this console
#                         (Ctrl+C stops it); the browser is never opened.
#   -NoBrowser            test/automation mode: never open a real browser.
#   -RuntimeRoot <dir>    target Runtime to display; default: this
#                         installation (the product Runtime Root).
#   -Port <n>             loopback port; default: pick a free 127.0.0.1 port.
# Non-authoritative instance metadata and logs live under
# <this installation>\web_console_data\ (created on demand; see
# docs/WEB_CONSOLE_DEVELOPMENT.md for the cleanup contract).
param(
    [int]$Port = 0,
    [string]$RuntimeRoot = "",
    [switch]$Foreground,
    [switch]$NoBrowser,
    [int]$HealthTimeoutSeconds = 30
)
$ErrorActionPreference = "Stop"

$ConsoleRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$ServerScript = Join-Path $ConsoleRoot "scripts\web_console_server.py"
$DataDir = Join-Path $ConsoleRoot "web_console_data"
$MetadataFile = Join-Path $DataDir "instance.json"

function Write-Fail([string]$Reason) {
    Write-Host "WEB_CONSOLE_START_FAILED reason=$Reason"
    exit 5
}

if (-not (Test-Path $ServerScript)) { Write-Fail "server script missing: $ServerScript" }
$Python = "python"
try { $null = Get-Command $Python -ErrorAction Stop } catch { Write-Fail "python not found on PATH" }

if ($RuntimeRoot) {
    try { $RuntimeRoot = (Resolve-Path $RuntimeRoot).Path } catch { Write-Fail "Runtime root does not exist: $RuntimeRoot" }
} else {
    $RuntimeRoot = $ConsoleRoot
}
if (-not (Test-Path (Join-Path $RuntimeRoot "scripts\supervisor_control.py"))) {
    Write-Fail "Runtime root has no v1.2 control plane (scripts/supervisor_control.py missing): $RuntimeRoot"
}

function Get-InstanceVerdict {
    # Shared freshness/ownership check; prints one JSON verdict document.
    $output = & $Python $ServerScript verify-instance --data-dir $DataDir --runtime-root $RuntimeRoot
    if ($LASTEXITCODE -ne 0) { Write-Fail "instance verification failed" }
    return ($output -join "`n") | ConvertFrom-Json
}

function New-FreeLoopbackPort {
    $listener = [System.Net.Sockets.TcpListener]::new([System.Net.IPAddress]::Loopback, 0)
    $listener.Start()
    try { return ([System.Net.IPEndPoint]$listener.LocalEndpoint).Port } finally { $listener.Stop() }
}

function Open-Browser([string]$Url) {
    if (-not $NoBrowser) { Start-Process $Url }
}

if ($HealthTimeoutSeconds -lt 1) { Write-Fail "HealthTimeoutSeconds must be >= 1" }
New-Item -ItemType Directory -Force -Path (Join-Path $DataDir "logs") | Out-Null

$verdict = Get-InstanceVerdict
if ($verdict.verdict -eq "VERIFIED") {
    $url = "http://127.0.0.1:$($verdict.instance.port)/"
    Write-Host "WEB_CONSOLE_ALREADY_RUNNING pid=$($verdict.instance.pid) port=$($verdict.instance.port) url=$url"
    Open-Browser $url
    exit 0
}
if ($verdict.verdict -eq "ALIVE_UNVERIFIED") {
    Write-Fail ("a live process is recorded but did not verify as this Runtime's console; " +
                "run STOP_WEB_CONSOLE.ps1 or remove web_console_data\instance.json after checking it")
}
if ($verdict.verdict -eq "FOREIGN") {
    Write-Fail "recorded instance metadata belongs to a different Runtime root"
}
if ($verdict.verdict -in @("STALE", "INVALID")) {
    Remove-Item -LiteralPath $MetadataFile -Force -ErrorAction SilentlyContinue
    Write-Host "WEB_CONSOLE_STALE_METADATA_REMOVED verdict=$($verdict.verdict)"
}

if ($Port -le 0) { $Port = New-FreeLoopbackPort }
$Url = "http://127.0.0.1:$Port/"
# The server owns its log file; the launcher must not redirect the server's
# stdio through inherited console pipes (a detached server holding an
# inherited pipe write-end would keep the launcher's own output pipes from
# reaching EOF and deadlock callers that read them).
$serverLog = Join-Path $DataDir "logs\server-$Port.log"
$serverArgs = @($ServerScript, "serve", "--host", "127.0.0.1", "--port", "$Port",
    "--runtime-root", $RuntimeRoot, "--data-dir", $DataDir)

if ($Foreground) {
    # Diagnostic mode keeps logging on this console (no --log-file).
    Write-Host "WEB_CONSOLE_FOREGROUND_START url=$Url (Ctrl+C stops the console)"
    & $Python @serverArgs
    exit $LASTEXITCODE
}

$stamp = Get-Date -Format "yyyyMMdd-HHmmss"
# The hidden background server writes its own log file; nothing redirects its
# stdio. Quote arguments containing whitespace; Start-Process joins the array.
$backgroundArgs = @($serverArgs + "--log-file", $serverLog)
$quoted = ($backgroundArgs | ForEach-Object {
    if ($_ -match '\s') { '"' + ($_ -replace '"', '\"') + '"' } else { $_ }
}) -join ' '
$proc = Start-Process -FilePath $Python -ArgumentList $quoted `
    -WorkingDirectory $ConsoleRoot -WindowStyle Hidden -PassThru

$deadline = (Get-Date).AddSeconds($HealthTimeoutSeconds)
while ($true) {
    if ($proc.HasExited) {
        $tail = ""
        if (Test-Path $serverLog) { $tail = (Get-Content $serverLog -Tail 5) -join ' | ' }
        Write-Fail "backend process exited during start: $tail"
    }
    $verdict = Get-InstanceVerdict
    if ($verdict.verdict -eq "VERIFIED") {
        Write-Host "WEB_CONSOLE_READY pid=$($proc.Id) port=$Port url=$Url"
        Open-Browser $Url
        exit 0
    }
    if ((Get-Date) -ge $deadline) { break }
    Start-Sleep -Milliseconds 500
}
Write-Fail ("backend did not become healthy within $HealthTimeoutSeconds seconds; " +
            "it may still be running: use STOP_WEB_CONSOLE.ps1")
