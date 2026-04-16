$ErrorActionPreference = "Continue"

$root = Split-Path -Parent $MyInvocation.MyCommand.Path
$pidFiles = @(
    Join-Path $root ".dev-backend.pid",
    Join-Path $root ".dev-frontend.pid"
)

foreach ($pidFile in $pidFiles) {
    if (-not (Test-Path $pidFile)) {
        continue
    }

    $pidValue = Get-Content $pidFile -ErrorAction SilentlyContinue
    if (-not $pidValue) {
        Remove-Item $pidFile -Force -ErrorAction SilentlyContinue
        continue
    }

    $proc = Get-Process -Id ([int]$pidValue) -ErrorAction SilentlyContinue
    if ($null -ne $proc) {
        try {
            Stop-Process -Id $proc.Id -Force -ErrorAction Stop
            Write-Output "Stopped PID $($proc.Id) ($($proc.ProcessName))"
        }
        catch {
            Write-Warning "Could not stop PID $pidValue from $pidFile: $($_.Exception.Message)"
        }
    }
    else {
        Write-Output "Process PID $pidValue not running"
    }

    Remove-Item $pidFile -Force -ErrorAction SilentlyContinue
}
