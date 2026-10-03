[CmdletBinding()]
param([switch]$IncludeInfrastructure)
$ErrorActionPreference = 'Stop'
$projectRoot = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot '..'))
$localRoot = Join-Path $projectRoot '.local'
New-Item -ItemType Directory -Force -Path $localRoot | Out-Null
. (Join-Path $PSScriptRoot 'dev-processes.ps1')
. (Join-Path $PSScriptRoot 'local-infrastructure.ps1')
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
    if ($IncludeInfrastructure) {
        $n8n = Get-OwnedN8nRecord $projectRoot
        if ($n8n) { Get-OwnedDevTree $n8n $projectRoot | Out-Null }
        Get-OwnedPostgres $projectRoot | Out-Null
    }
    foreach ($record in $allRecords) { Stop-OwnedDevTree $record $projectRoot }
    Save-DevRecords $devMarker @()
    if (Test-Path -LiteralPath $workerMarker) { Remove-Item -LiteralPath $workerMarker }
    if ($IncludeInfrastructure) {
        & (Join-Path $PSScriptRoot 'stop-n8n.ps1')
        & (Join-Path $PSScriptRoot 'stop-postgres.ps1')
        Write-Host 'FLOWBRIDGE STOPPED'
    } else { Write-Host 'Owned app/UI/worker process trees stopped. PostgreSQL and n8n remain available.' }
} finally { $launchLock.Dispose() }
