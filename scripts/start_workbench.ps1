$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $PSScriptRoot
Set-Location $root
$url = 'http://127.0.0.1:8765/'
$logDir = Join-Path $root 'logs'
New-Item -ItemType Directory -Force -Path $logDir | Out-Null

function Test-SafeLens {
    try {
        $response = Invoke-WebRequest -UseBasicParsing -Uri $url -TimeoutSec 2
        return ($response.StatusCode -eq 200)
    } catch {
        return $false
    }
}

if (Test-SafeLens) {
    try {
        $state = Invoke-RestMethod -Uri ($url + 'api/app/state') -TimeoutSec 2
        if ($state.clients -gt 0) {
            Invoke-RestMethod -Method Post -Uri ($url + 'api/app/refresh') -ContentType 'application/json' -Body '{}' -TimeoutSec 2 | Out-Null
            Write-Host 'SafeLens is already open. Refreshed the existing tab.'
            exit 0
        }
    } catch {
        # Fall through and open a browser if the presence endpoint is unavailable.
    }
    Start-Process $url
    Write-Host 'SafeLens is already running. Opened in your browser.'
    exit 0
}

$python = (Get-Command python.exe -ErrorAction SilentlyContinue).Source
if (-not $python) {
    Write-Host 'Python was not found. Install Python 3.10 or newer first.'
    exit 1
}

$stdout = Join-Path $logDir 'server.stdout.log'
$stderr = Join-Path $logDir 'server.stderr.log'
$process = Start-Process -FilePath $python -ArgumentList @('app\server.py', '--port', '8765') -WorkingDirectory $root -WindowStyle Hidden -RedirectStandardOutput $stdout -RedirectStandardError $stderr -PassThru

for ($i = 0; $i -lt 60; $i++) {
    Start-Sleep -Milliseconds 250
    if (Test-SafeLens) {
        Start-Process $url
        Write-Host 'SafeLens started. Opened in your browser.'
        exit 0
    }
    $process.Refresh()
    if ($process.HasExited) {
        Write-Host "SafeLens failed to start (exit code $($process.ExitCode))."
        if (Test-Path $stderr) { Get-Content $stderr -Tail 30 }
        exit 1
    }
}

Write-Host 'SafeLens did not respond in time. See logs\server.stderr.log.'
exit 1
