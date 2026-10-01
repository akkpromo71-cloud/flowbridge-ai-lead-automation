[CmdletBinding()]
param()
$ErrorActionPreference = 'Stop'
$projectRoot = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot '..'))
$runtimeRoot = Join-Path $env:LOCALAPPDATA 'AILeadAutomationPro\n8n-runtime'
$markerPath = Join-Path $runtimeRoot 'project-owner.json'
if (Test-Path -LiteralPath $runtimeRoot) {
    if (-not (Test-Path -LiteralPath $markerPath)) { throw 'Existing n8n runtime directory has no project ownership marker.' }
    $owner = Get-Content -LiteralPath $markerPath -Raw | ConvertFrom-Json
    if ($owner.projectRoot -ne $projectRoot -or $owner.purpose -ne 'synthetic-demo-runtime') {
        throw 'Existing n8n runtime belongs to a different project.'
    }
} else {
    New-Item -ItemType Directory -Path $runtimeRoot -Force | Out-Null
    @{ projectRoot=$projectRoot; purpose='synthetic-demo-runtime' } | ConvertTo-Json |
        Set-Content -LiteralPath $markerPath -Encoding UTF8
}
$cliPath = Join-Path $runtimeRoot 'node_modules\n8n\bin\n8n'
if (Test-Path -LiteralPath $cliPath) {
    & node $cliPath --version
    if ($LASTEXITCODE -ne 0) { throw 'The local n8n runtime could not run.' }
    Write-Host 'Existing project runtime preserved. Stop it before an intentional dependency refresh.'
    exit 0
}
Copy-Item -LiteralPath (Join-Path $PSScriptRoot 'package.json') -Destination (Join-Path $runtimeRoot 'package.json')
Copy-Item -LiteralPath (Join-Path $PSScriptRoot 'package-lock.json') -Destination (Join-Path $runtimeRoot 'package-lock.json')
& npm.cmd ci --prefix $runtimeRoot --ignore-scripts --no-fund --no-audit
if ($LASTEXITCODE -ne 0) { throw 'Installing the pinned n8n dependency tree failed.' }
& node $cliPath --version
if ($LASTEXITCODE -ne 0) { throw 'The local n8n runtime could not run.' }
