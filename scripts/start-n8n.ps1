[CmdletBinding()]
param([switch]$Setup, [ValidateRange(1,600)][int]$TimeoutSeconds=600)
$ErrorActionPreference = 'Stop'
$projectRoot = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot '..'))
. (Join-Path $PSScriptRoot 'dev-processes.ps1')
. (Join-Path $PSScriptRoot 'local-infrastructure.ps1')
$existing = Get-OwnedN8nRecord $projectRoot
if ($existing -and -not $Setup) {
    Wait-DevService $existing 5681 'http://127.0.0.1:5681/healthz/readiness' $TimeoutSeconds
    Write-Host 'n8n: already running'
    return
}
$manager = Join-Path $projectRoot 'n8n\manage-local.mjs'
if ($Setup) {
    & node $manager setup
    if ($LASTEXITCODE -ne 0) { throw 'Local n8n workflow setup failed.' }
}
& node $manager start "--ready-timeout=$TimeoutSeconds"
if ($LASTEXITCODE -ne 0) { throw 'Local n8n startup failed.' }
$record = Get-OwnedN8nRecord $projectRoot
if (-not $record) { throw 'n8n did not create a verified process record.' }
Wait-DevService $record 5681 'http://127.0.0.1:5681/healthz/readiness' $TimeoutSeconds
