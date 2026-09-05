$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $MyInvocation.MyCommand.Path
$Control = Join-Path $Root "control"
New-Item -ItemType Directory -Force -Path $Control | Out-Null
Set-Content -Path (Join-Path $Control "STOP") -Value "USER_STOP" -Encoding UTF8
Write-Host "control/STOP created. The orchestrator will make no new Codex calls after detecting it." -ForegroundColor Yellow
