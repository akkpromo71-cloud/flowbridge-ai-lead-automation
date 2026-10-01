[CmdletBinding()]
param([ValidateSet('demo','controlled')][string]$Profile='demo')
$ErrorActionPreference = 'Stop'
$projectRoot = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot '..'))
$runtimeRoot = Join-Path $env:LOCALAPPDATA "AILeadAutomationPro\n8n-$Profile"
$marker = Join-Path $runtimeRoot 'project-owner.json'
$recordPath = Join-Path $runtimeRoot 'process.json'
if (-not (Test-Path -LiteralPath $marker)) { throw 'Project n8n ownership marker is missing.' }
$owner = Get-Content -LiteralPath $marker -Raw | ConvertFrom-Json
if ($owner.root -ne $projectRoot -or $owner.purpose -ne "synthetic-$Profile") { throw 'Refusing to stop n8n belonging to another project.' }
if (-not (Test-Path -LiteralPath $recordPath)) { Write-Host 'Project n8n is already stopped.'; exit 0 }
$record = Get-Content -LiteralPath $recordPath -Raw | ConvertFrom-Json
$expectedCli = Join-Path $env:LOCALAPPDATA 'AILeadAutomationPro\n8n-runtime\node_modules\n8n\bin\n8n'
if ($record.cliPath -ne $expectedCli) { throw 'Unexpected n8n process record.' }
$process = Get-CimInstance Win32_Process -Filter "ProcessId = $($record.pid)" -ErrorAction SilentlyContinue
if ($process) {
    if ($process.Name -ne 'node.exe' -or -not $process.CommandLine.Contains($expectedCli)) {
        throw 'PID now belongs to another process; it was not stopped.'
    }
    Stop-Process -Id $record.pid -ErrorAction Stop
    Write-Host 'Project n8n stopped. Database, credentials and encryption key were preserved.'
} else { Write-Host 'Project n8n is already stopped.' }
Remove-Item -LiteralPath $recordPath
