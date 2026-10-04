# Registers two Windows scheduled tasks:
#   JobSearchAgent        - daily 9:00 AM: search, analyse, email the digest
#   JobSearchAgentReplies - every 30 min: act on APPLY / SKIP / APPLIED emails you send
# Run once:  powershell -ExecutionPolicy Bypass -File register_task.ps1
# Remove:    Unregister-ScheduledTask -TaskName "JobSearchAgent*" -Confirm:$false

$python = "$env:LOCALAPPDATA\Programs\Python\Python312\pythonw.exe"
$script = Join-Path $PSScriptRoot "job_agent.py"
# StartWhenAvailable: if the PC was off at the scheduled time, run as soon as it's back on.
$settings = New-ScheduledTaskSettingsSet -StartWhenAvailable -RunOnlyIfNetworkAvailable -ExecutionTimeLimit (New-TimeSpan -Minutes 15)

$daily = New-ScheduledTaskAction -Execute $python -Argument "`"$script`"" -WorkingDirectory $PSScriptRoot
Register-ScheduledTask -TaskName "JobSearchAgent" -Action $daily -Trigger (New-ScheduledTaskTrigger -Daily -At 9:00AM) `
    -Settings $settings -Description "Daily job search digest email" -Force | Out-Null

$replies = New-ScheduledTaskAction -Execute $python -Argument "`"$script`" check-replies" -WorkingDirectory $PSScriptRoot
$every30 = New-ScheduledTaskTrigger -Once -At (Get-Date) -RepetitionInterval (New-TimeSpan -Minutes 30)
Register-ScheduledTask -TaskName "JobSearchAgentReplies" -Action $replies -Trigger $every30 `
    -Settings $settings -Description "Job agent: process APPLY/SKIP reply emails" -Force | Out-Null

Write-Output "Registered 'JobSearchAgent' (daily 9:00 AM) and 'JobSearchAgentReplies' (every 30 min)."
