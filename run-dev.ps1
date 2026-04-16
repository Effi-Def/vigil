param(
    [int]$BackendPort = 8001,
    [int]$FrontendPort = 5173,
    [string]$BackendHost = "127.0.0.1"
)

$ErrorActionPreference = "Stop"

$root = Split-Path -Parent $MyInvocation.MyCommand.Path
$frontendDir = Join-Path $root "vigil-frontend"
$venvPython = Join-Path $root "venv\Scripts\python.exe"

if (-not (Test-Path $venvPython)) {
    throw "Python venv not found at $venvPython"
}
if (-not (Test-Path $frontendDir)) {
    throw "Frontend folder not found at $frontendDir"
}

function Test-PortListening {
    param([int]$Port)
    $conn = Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue
    return $null -ne $conn
}

if (Test-PortListening -Port $BackendPort) {
    Write-Warning "Backend port $BackendPort is already in use."
}
if (Test-PortListening -Port $FrontendPort) {
    Write-Warning "Frontend port $FrontendPort is already in use."
}

$logsDir = Join-Path $root ".logs"
New-Item -ItemType Directory -Path $logsDir -Force | Out-Null

$backendOut = Join-Path $logsDir "backend.out.log"
$backendErr = Join-Path $logsDir "backend.err.log"
$frontendOut = Join-Path $logsDir "frontend.out.log"
$frontendErr = Join-Path $logsDir "frontend.err.log"

$backendArgs = @(
    "-m", "uvicorn",
    "main:app",
    "--host", $BackendHost,
    "--port", "$BackendPort"
)

$backendProc = Start-Process `
    -FilePath $venvPython `
    -ArgumentList $backendArgs `
    -WorkingDirectory $root `
    -PassThru `
    -WindowStyle Minimized `
    -RedirectStandardOutput $backendOut `
    -RedirectStandardError $backendErr

$frontendCmd = @"
`$env:VITE_API_TARGET = 'http://localhost:$BackendPort'
`$env:VITE_DEV_PORT = '$FrontendPort'
Set-Location '$frontendDir'
npm run dev
"@
$frontendProc = Start-Process `
    -FilePath "powershell.exe" `
    -ArgumentList @("-NoLogo", "-NoProfile", "-Command", $frontendCmd) `
    -WorkingDirectory $frontendDir `
    -PassThru `
    -WindowStyle Minimized `
    -RedirectStandardOutput $frontendOut `
    -RedirectStandardError $frontendErr

Set-Content -Path (Join-Path $root ".dev-backend.pid") -Value $backendProc.Id
Set-Content -Path (Join-Path $root ".dev-frontend.pid") -Value $frontendProc.Id

Write-Output "Started backend PID $($backendProc.Id) on http://${BackendHost}:$BackendPort"
Write-Output "Started frontend PID $($frontendProc.Id) on http://localhost:$FrontendPort"
Write-Output "Logs: $logsDir"
