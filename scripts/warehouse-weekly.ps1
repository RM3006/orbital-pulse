param(
    [ValidateSet('Install', 'Status', 'Run', 'DryRun')]
    [string]$Action = 'Status',
    [string]$TaskName = 'OrbitalPulse-Warehouse'
)
$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $PSScriptRoot
$python = Join-Path $projectRoot '.venv\Scripts\python.exe'
$pythonWindowless = Join-Path $projectRoot '.venv\Scripts\pythonw.exe'
$expectedScript = Join-Path $PSScriptRoot 'warehouse_weekly.py'
$workerTaskName = 'OrbitalPulse-LL2'

if ($TaskName -ne 'OrbitalPulse-Warehouse') {
    throw 'Use the project-specific task name OrbitalPulse-Warehouse.'
}
if (-not (Test-Path -LiteralPath $python) -or -not (Test-Path -LiteralPath $pythonWindowless)) {
    throw 'Run uv sync --locked before installing the warehouse task.'
}
$existingTask = Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
if ($existingTask) {
    $taskAction = @($existingTask.Actions)[0]
    if ($taskAction.Execute -ne $pythonWindowless -or
        $taskAction.WorkingDirectory -ne $projectRoot -or
        $taskAction.Arguments -notlike "*$expectedScript*") {
        throw 'Existing warehouse task belongs to another command or checkout; refusing to replace it.'
    }
}

Push-Location -LiteralPath $projectRoot
try {
    switch ($Action) {
        'Install' {
            $workerTask = Get-ScheduledTask -TaskName $workerTaskName -ErrorAction Stop
            if ($workerTask.State -eq 'Disabled') {
                throw 'Resume the verified LL2 worker before installing the warehouse schedule.'
            }
            $taskAction = New-ScheduledTaskAction -Execute $pythonWindowless `
                -Argument "`"$expectedScript`"" `
                -WorkingDirectory $projectRoot
            $trigger = New-ScheduledTaskTrigger -Daily -At (Get-Date -Hour 15 -Minute 0 -Second 0)
            $settings = New-ScheduledTaskSettingsSet -MultipleInstances IgnoreNew `
                -StartWhenAvailable -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries `
                -ExecutionTimeLimit (New-TimeSpan -Hours 2)
            $userName = [System.Security.Principal.WindowsIdentity]::GetCurrent().Name
            $principal = New-ScheduledTaskPrincipal -UserId $userName -LogonType Interactive -RunLevel Limited
            Register-ScheduledTask -TaskName $TaskName -Action $taskAction -Trigger $trigger `
                -Settings $settings -Principal $principal -Description `
                'Daily local check; publishes only a new, complete LL2 snapshot after local account-scope confirmation and project usage reserves.' `
                -Force | Out-Null
            Write-Output "Installed $TaskName for daily 15:00 local checks."
        }
        'Status' {
            if (-not $existingTask) {
                Write-Output "$TaskName is not installed."
            } else {
                Get-ScheduledTask -TaskName $TaskName | Select-Object TaskName,State,TaskPath
                Get-ScheduledTaskInfo -TaskName $TaskName |
                    Select-Object LastRunTime,LastTaskResult,NextRunTime
            }
        }
        'Run' {
            & $python $expectedScript
            if ($LASTEXITCODE -ne 0) { throw "Warehouse scheduler exited with code $LASTEXITCODE." }
        }
        'DryRun' {
            & $python $expectedScript --dry-run
            if ($LASTEXITCODE -ne 0) { throw "Warehouse dry-run exited with code $LASTEXITCODE." }
        }
    }
}
finally {
    Pop-Location
}
