param(
    [ValidateSet('Install', 'Status', 'Stop', 'Resume', 'Uninstall')]
    [string]$Action = 'Status',
    [string]$HistoryPlanId = 'p2-history-1957-2026',
    [string]$Reason,
    [string]$TaskName = 'OrbitalPulse-LL2'
)
$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $PSScriptRoot
$dataRoot = Join-Path $projectRoot 'data'
$python = Join-Path $projectRoot '.venv\Scripts\python.exe'
$pythonWindowless = Join-Path $projectRoot '.venv\Scripts\pythonw.exe'
if (-not (Test-Path -LiteralPath $pythonWindowless)) {
    throw 'Run uv sync --locked before installing the worker.'
}
if ($TaskName -notmatch '^OrbitalPulse-LL2(?:-[A-Za-z0-9_-]+)?$') {
    throw 'Use the OrbitalPulse-LL2 task name or a project-specific suffix.'
}
$existingTask = Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
if ($existingTask) {
    $taskAction = @($existingTask.Actions)[0]
    if ($taskAction.Execute -ne $pythonWindowless -or $taskAction.WorkingDirectory -ne $projectRoot) {
        throw 'Existing task belongs to a different checkout; refusing to replace or control it.'
    }
}
$workerArguments = "-m launch_atlas worker-tick --data-dir `"$dataRoot`""
$workerDescription = 'Resumable local LL2 refresh with durable local state; no dbt or warehouse publication.'
if ($Action -eq 'Install') {
    $projectEnvironment = @{}
    $envPath = Join-Path $projectRoot '.env.local'
    if (Test-Path -LiteralPath $envPath) {
        foreach ($line in Get-Content -LiteralPath $envPath) {
            if ($line -match '^\s*(GCP_PROJECT_ID|GCP_LOCATION)\s*=\s*(.*?)\s*$') {
                $projectEnvironment[$Matches[1]] = $Matches[2].Trim().Trim('"').Trim("'")
            }
        }
    }
    $projectId = $projectEnvironment['GCP_PROJECT_ID']
    $location = $projectEnvironment['GCP_LOCATION']
    if ([bool]$projectId -ne [bool]$location) {
        throw '.env.local must contain both GCP_PROJECT_ID and GCP_LOCATION to enable raw archiving.'
    }
    if ($projectId) {
        if ($projectId -notmatch '^[A-Za-z0-9-]+$' -or $location -notmatch '^[a-z0-9-]+$') {
            throw 'GCP project ID and location in .env.local must contain only letters, digits and hyphens.'
        }
        $workerArguments += " --archive-raw-to-bigquery --project-id `"$projectId`" --location `"$location`""
        $workerDescription = 'Resumable local LL2 refresh; archives exact responses to BigQuery and retains local state; no dbt or warehouse publication.'
    }
}
Push-Location -LiteralPath $projectRoot
try {
    switch ($Action) {
        'Install' {
            & $python -m launch_atlas worker-init --history-plan-id $HistoryPlanId --data-dir $dataRoot
            if ($LASTEXITCODE -ne 0) { throw 'Worker initialization failed.' }
            $userName = [System.Security.Principal.WindowsIdentity]::GetCurrent().Name
            $taskAction = New-ScheduledTaskAction -Execute $pythonWindowless `
                -Argument $workerArguments `
                -WorkingDirectory $projectRoot
            $triggers = @(
                (New-ScheduledTaskTrigger -Once -At (Get-Date).AddMinutes(1) `
                    -RepetitionInterval (New-TimeSpan -Minutes 5)),
                (New-ScheduledTaskTrigger -AtLogOn -User $userName)
            )
            $settings = New-ScheduledTaskSettingsSet -MultipleInstances IgnoreNew `
                -StartWhenAvailable -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries `
                -ExecutionTimeLimit (New-TimeSpan -Hours 1)
            $principal = New-ScheduledTaskPrincipal -UserId $userName -LogonType Interactive -RunLevel Limited
            Register-ScheduledTask -TaskName $TaskName -Action $taskAction -Trigger $triggers `
                -Settings $settings -Principal $principal -Description $workerDescription `
                -Force | Out-Null
            Start-ScheduledTask -TaskName $TaskName
        }
        'Stop' {
            & $python -m launch_atlas worker-stop --data-dir $dataRoot
            if ($LASTEXITCODE -ne 0) { throw 'Stop request failed.' }
            if ($existingTask) { Disable-ScheduledTask -TaskName $TaskName | Out-Null }
        }
        'Resume' {
            if ([string]::IsNullOrWhiteSpace($Reason)) { throw 'Supply -Reason after investigating a failure.' }
            & $python -m launch_atlas worker-resume --reason $Reason --data-dir $dataRoot
            if ($LASTEXITCODE -ne 0) { throw 'Worker resume failed.' }
            if ($existingTask) {
                Enable-ScheduledTask -TaskName $TaskName | Out-Null
                Start-ScheduledTask -TaskName $TaskName
            }
        }
        'Uninstall' {
            & $python -m launch_atlas worker-stop --data-dir $dataRoot
            if ($LASTEXITCODE -ne 0) { throw 'Stop request failed.' }
            if ($existingTask) {
                Disable-ScheduledTask -TaskName $TaskName | Out-Null
                Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false
            }
        }
    }
    & $python -m launch_atlas worker-status --data-dir $dataRoot
    if ($LASTEXITCODE -ne 0) { throw 'Worker status failed.' }
    $registeredTask = Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
    if ($registeredTask) {
        Get-ScheduledTaskInfo -TaskName $TaskName |
            Select-Object TaskName, LastRunTime, LastTaskResult, NextRunTime
    }
}
finally {
    Pop-Location
}
