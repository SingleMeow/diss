<#
    start.ps1 - launches the backend (FastAPI/uvicorn) and the frontend (Vite)
    together in one window. Press Ctrl+C to stop both.

    Usage:
        powershell -ExecutionPolicy Bypass -File .\start.ps1
#>

$ErrorActionPreference = "Stop"
$root     = $PSScriptRoot
$backend  = Join-Path $root "backend"
$frontend = Join-Path $root "frontend"
$python   = Join-Path $backend ".venv\Scripts\python.exe"

if (-not (Test-Path $python)) {
    Write-Error "Backend venv not found at $python. Create it and run: pip install -r backend\requirements.txt"
    exit 1
}
if (-not (Test-Path (Join-Path $frontend "node_modules"))) {
    Write-Host "frontend\node_modules missing - installing dependencies..." -ForegroundColor Yellow
    Push-Location $frontend
    npm install
    Pop-Location
}

$procs = @()

function Stop-All {
    Write-Host "`nShutting down..." -ForegroundColor Cyan
    foreach ($p in $procs) {
        if ($p -and -not $p.HasExited) {
            try { taskkill /PID $p.Id /T /F | Out-Null } catch {}
        }
    }
}

try {
    Write-Host "Starting backend  -> http://127.0.0.1:8000  (docs: /docs)" -ForegroundColor Green
    $backendArgs = @("-m", "uvicorn", "app.main:app", "--reload", "--host", "127.0.0.1", "--port", "8000")
    $procs += Start-Process -FilePath $python -ArgumentList $backendArgs -WorkingDirectory $backend -NoNewWindow -PassThru

    Write-Host "Starting frontend -> http://127.0.0.1:5173" -ForegroundColor Green
    $procs += Start-Process -FilePath "npm.cmd" -ArgumentList @("run", "dev") -WorkingDirectory $frontend -NoNewWindow -PassThru

    Write-Host "`nBoth services running. Press Ctrl+C to stop.`n" -ForegroundColor Cyan

    while ($true) {
        Start-Sleep -Seconds 1
        foreach ($p in $procs) {
            if ($p.HasExited) {
                Write-Host ("A service exited (PID " + $p.Id + ", code " + $p.ExitCode + "). Stopping the other.") -ForegroundColor Red
                Stop-All
                exit $p.ExitCode
            }
        }
    }
}
finally {
    Stop-All
}
