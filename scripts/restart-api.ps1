$ErrorActionPreference = 'Stop'
$projectRoot = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot '..'))
$localRoot = Join-Path $projectRoot '.local'
New-Item -ItemType Directory -Force -Path $localRoot | Out-Null
. (Join-Path $PSScriptRoot 'dev-processes.ps1')
$launchLock = Enter-ProjectLaunchLock $projectRoot
try {
    $marker = Join-Path $localRoot 'dev-processes.json'
    $records = @(Get-LiveDevRecords $marker)
    $apiRecords = @($records | Where-Object { $_.name -eq 'api' })
    foreach ($record in $apiRecords) { Stop-OwnedDevTree $record $projectRoot }
    $records = @($records | Where-Object { $_.name -ne 'api' })
    Save-DevRecords $marker $records
    if (Get-NetTCPConnection -State Listen -LocalPort 8000 -ErrorAction SilentlyContinue) {
        throw 'Port 8000 is used; no unrelated process was stopped.'
    }
    $pythonExe = Join-Path $projectRoot '.venv\Scripts\python.exe'
    $api = Start-Process -FilePath $pythonExe -ArgumentList @('-m','uvicorn','app.main:create_app','--factory','--host','127.0.0.1','--port','8000') -WorkingDirectory $projectRoot -WindowStyle Hidden -PassThru -RedirectStandardOutput (Join-Path $localRoot 'api.out.log') -RedirectStandardError (Join-Path $localRoot 'api.err.log')
    $record = @{name='api';pid=$api.Id;path=$pythonExe;startedAt=$api.StartTime.ToUniversalTime().ToString('o')}
    $records += $record
    Save-DevRecords $marker $records
    Wait-DevService $record 8000 'http://127.0.0.1:8000/health/ready'
    Write-Host 'Owned API restarted and readiness verified.'
} finally { $launchLock.Dispose() }
