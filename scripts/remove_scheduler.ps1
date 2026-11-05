$ErrorActionPreference = "Stop"
$TaskName = "SafetyScienceMonitor_Daily0800"
try {
    Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false -ErrorAction Stop
    [pscustomobject]@{ ok = $true; task = $TaskName } | ConvertTo-Json -Compress
} catch {
    [pscustomobject]@{ ok = $false; error = $_.Exception.Message } | ConvertTo-Json -Compress
    exit 1
}
