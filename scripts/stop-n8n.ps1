[CmdletBinding()]
param([ValidateSet('demo','controlled')][string]$Profile='demo')
$ErrorActionPreference = 'Stop'
$projectRoot = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot '..'))
$runtimeRoot = Join-Path $env:LOCALAPPDATA "AILeadAutomationPro\n8n-$Profile"
$recordPath = Join-Path $runtimeRoot 'process.json'
. (Join-Path $PSScriptRoot 'dev-processes.ps1')
. (Join-Path $PSScriptRoot 'local-infrastructure.ps1')
$record = Get-OwnedN8nRecord $projectRoot $Profile
if ($record) {
    Stop-OwnedDevTree $record $projectRoot
    Write-Host 'Project n8n stopped. Database, credentials and encryption key were preserved.'
} else { Write-Host 'Project n8n is already stopped.' }
if (Test-Path -LiteralPath $recordPath) { Remove-Item -LiteralPath $recordPath }
