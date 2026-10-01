[CmdletBinding()]
param([switch]$Setup)
$ErrorActionPreference = 'Stop'
$projectRoot = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot '..'))
$manager = Join-Path $projectRoot 'n8n\manage-local.mjs'
if ($Setup) {
    & node $manager setup
    if ($LASTEXITCODE -ne 0) { throw 'Local n8n workflow setup failed.' }
}
& node $manager start
if ($LASTEXITCODE -ne 0) { throw 'Local n8n startup failed.' }
