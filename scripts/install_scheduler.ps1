param(
    [string]$DailyTime = "08:00",
    [string]$TaskName = "SafetyScienceMonitor_Daily0800"
)
$ErrorActionPreference = "Stop"
try {
    $root = Split-Path -Parent $PSScriptRoot
    $python = (Get-Command python -ErrorAction Stop).Source
    $script = Join-Path $root "app\sync_cli.py"
    if (-not (Test-Path $script)) { throw "sync_cli.py not found: $script" }
    $argument = '"' + $script + '" --trigger scheduled --quiet'
    $action = New-ScheduledTaskAction -Execute $python -Argument $argument -WorkingDirectory $root
    $trigger = New-ScheduledTaskTrigger -Daily -At $DailyTime
    $settings = New-ScheduledTaskSettingsSet -StartWhenAvailable -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -ExecutionTimeLimit (New-TimeSpan -Minutes 45)
    $user = [System.Security.Principal.WindowsIdentity]::GetCurrent().Name
    $principal = New-ScheduledTaskPrincipal -UserId $user -LogonType Interactive -RunLevel Limited
    Register-ScheduledTask -TaskName $TaskName -Action $action -Trigger $trigger -Settings $settings -Principal $principal -Description "SafeLens daily journal check" -Force | Out-Null
    $task = Get-ScheduledTask -TaskName $TaskName
    $info = Get-ScheduledTaskInfo -TaskName $TaskName
    [pscustomobject]@{
        ok = $true
        task = $TaskName
        daily_time = $DailyTime
        state = [string]$task.State
        next_run = if ($info.NextRunTime) { $info.NextRunTime.ToString("yyyy-MM-dd HH:mm") } else { "" }
        python = $python
    } | ConvertTo-Json -Compress
    exit 0
} catch {
    [pscustomobject]@{ ok = $false; error = $_.Exception.Message } | ConvertTo-Json -Compress
    exit 1
}
