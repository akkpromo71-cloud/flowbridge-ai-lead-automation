$ErrorActionPreference = 'Stop'
$projectRoot = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot '..'))
$localRoot = Join-Path $projectRoot '.local'
New-Item -ItemType Directory -Force -Path $localRoot | Out-Null
. (Join-Path $PSScriptRoot 'dev-processes.ps1')
$launchLock = Enter-ProjectLaunchLock $projectRoot
try {
    $devMarker = Join-Path $localRoot 'dev-processes.json'
    $workerMarker = Join-Path $localRoot 'worker-process.json'
    $records = @(Get-LiveDevRecords $devMarker)
    $workerRecords = @()
    if (Test-Path -LiteralPath $workerMarker) {
        $worker = Get-Content -LiteralPath $workerMarker -Raw | ConvertFrom-Json
        $worker | Add-Member -NotePropertyName name -NotePropertyValue 'worker' -Force
        if (Get-OwnedDevProcess $worker) { $workerRecords += $worker }
    }
    # Stop the worker first, then UI/API. Validate every tree before any stop.
    $allRecords = @($workerRecords) + @($records)
    foreach ($record in $allRecords) { Get-OwnedDevTree $record $projectRoot | Out-Null }
    foreach ($record in $allRecords) { Stop-OwnedDevTree $record $projectRoot }
    Save-DevRecords $devMarker @()
    if (Test-Path -LiteralPath $workerMarker) { Remove-Item -LiteralPath $workerMarker }
    Write-Host 'Owned app/UI/worker process trees stopped. PostgreSQL and n8n remain available.'
} finally { $launchLock.Dispose() }
