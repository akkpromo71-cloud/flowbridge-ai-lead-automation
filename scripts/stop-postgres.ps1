[CmdletBinding()]
param()

$ErrorActionPreference = 'Stop'
$projectRoot = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot '..'))
$runtimeRoot = Join-Path $env:LOCALAPPDATA 'AILeadAutomationPro\postgres-17-demo-test'
$markerPath = Join-Path $runtimeRoot 'project-owner.json'
$dataRoot = Join-Path $runtimeRoot 'data'
$pgCtl = Join-Path $projectRoot '.local\postgresql-17.11-4\pgsql\bin\pg_ctl.exe'
. (Join-Path $PSScriptRoot 'local-infrastructure.ps1')
if (-not (Get-OwnedPostgres $projectRoot)) {
    Write-Host 'Project PostgreSQL is already stopped.'
    return
}
if (-not (Test-Path -LiteralPath $pgCtl)) { throw 'The project PostgreSQL executable was not found.' }
$arguments = @('stop', '-D', ('"' + $dataRoot + '"'), '-m', 'fast', '-w', '-t', '30')
$launcher = Start-Process -FilePath $pgCtl -ArgumentList $arguments -WindowStyle Hidden -PassThru
if (-not $launcher.WaitForExit(35000)) { throw 'PostgreSQL shutdown timed out.' }
$launcher.Refresh()
if ($launcher.ExitCode -ne 0) { throw 'PostgreSQL could not be stopped cleanly.' }
Write-Host 'Project PostgreSQL stopped. The data directory has been preserved.'
