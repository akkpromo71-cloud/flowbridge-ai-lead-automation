[CmdletBinding()]
param([switch]$SkipN8n, [switch]$SkipWorker)
$ErrorActionPreference = 'Stop'
$projectRoot = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot '..'))
$localRoot = Join-Path $projectRoot '.local'
$pythonExe = Join-Path $projectRoot '.venv\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $pythonExe)) { throw 'Install project dependencies first; see README.' }
New-Item -ItemType Directory -Force -Path $localRoot | Out-Null
. (Join-Path $PSScriptRoot 'dev-processes.ps1')
$launchLock = Enter-ProjectLaunchLock $projectRoot
Push-Location $projectRoot
try {
    & (Join-Path $PSScriptRoot 'start-postgres.ps1')
    if (-not (Test-Path -LiteralPath (Join-Path $projectRoot '.env'))) {
        & $pythonExe -m app.cli init-env
        if ($LASTEXITCODE -ne 0) { throw 'Environment setup failed.' }
    }
    & $pythonExe -m alembic upgrade head
    if ($LASTEXITCODE -ne 0) { throw 'Migration failed.' }
    if (-not $SkipN8n) { & (Join-Path $PSScriptRoot 'start-n8n.ps1') }

    $processMarker = Join-Path $localRoot 'dev-processes.json'
    $records = @(Get-LiveDevRecords $processMarker)
    Save-DevRecords $processMarker $records
    $specs = @(
        @{name='api';port=8000;url='http://127.0.0.1:8000/health/ready';file=$pythonExe;args=@('-m','uvicorn','app.main:create_app','--factory','--host','127.0.0.1','--port','8000')},
        @{name='frontend';port=5173;url='http://127.0.0.1:5173/';file=(Get-Command node.exe).Source;args=@('node_modules/vite/bin/vite.js','--host','127.0.0.1','--port','5173','--strictPort');cwd=(Join-Path $projectRoot 'frontend')}
    )
    foreach ($spec in $specs) {
        $existing = @($records | Where-Object { $_.name -eq $spec.name })
        if ($existing.Count -gt 1) { throw "Multiple recorded $($spec.name) processes are alive; inspect them before continuing." }
        if ($existing.Count -eq 1) {
            # An owned launcher may still be loading before its TCP port appears.
            # Wait for it instead of starting another copy.
            Wait-DevService $existing[0] $spec.port $spec.url
            Write-Host "$($spec.name): existing project process is ready."
            continue
        }
        if (Get-NetTCPConnection -State Listen -LocalPort $spec.port -ErrorAction SilentlyContinue) {
            throw "Port $($spec.port) is occupied by an unrecorded process; it was not changed."
        }
        $workingDir = if ($spec.cwd) { $spec.cwd } else { $projectRoot }
        $process = Start-Process -FilePath $spec.file -ArgumentList $spec.args -WorkingDirectory $workingDir -WindowStyle Hidden -PassThru -RedirectStandardOutput (Join-Path $localRoot "$($spec.name).out.log") -RedirectStandardError (Join-Path $localRoot "$($spec.name).err.log")
        $record = @{name=$spec.name;pid=$process.Id;path=$spec.file;startedAt=$process.StartTime.ToUniversalTime().ToString('o')}
        $records += $record
        Save-DevRecords $processMarker $records
        Wait-DevService $record $spec.port $spec.url
        Write-Host "$($spec.name): ready."
    }

    $workerMarker = Join-Path $localRoot 'worker-process.json'
    $workerRunning = $false
    if (Test-Path -LiteralPath $workerMarker) {
        $workerRecord = Get-Content -LiteralPath $workerMarker -Raw | ConvertFrom-Json
        $workerRecord | Add-Member -NotePropertyName name -NotePropertyValue 'worker' -Force
        $workerRunning = $null -ne (Get-OwnedDevProcess $workerRecord)
    }
    if (-not $SkipWorker -and -not $workerRunning) {
        $worker = Start-Process -FilePath $pythonExe -ArgumentList @('-m','app.worker') -WorkingDirectory $projectRoot -WindowStyle Hidden -PassThru -RedirectStandardOutput (Join-Path $localRoot 'worker.out.log') -RedirectStandardError (Join-Path $localRoot 'worker.err.log')
        $workerRecord = @{name='worker';pid=$worker.Id;path=$pythonExe;startedAt=$worker.StartTime.ToUniversalTime().ToString('o')}
        $workerRecord | ConvertTo-Json | Set-Content -LiteralPath $workerMarker
        Start-Sleep -Milliseconds 750
        if (-not (Get-OwnedDevProcess $workerRecord)) { throw 'Worker exited at startup; inspect .local/worker.err.log.' }
        Write-Host 'worker: started.'
    } elseif (-not $SkipWorker) { Write-Host 'worker: existing project process is running.' }
    Write-Host 'Local UI: http://127.0.0.1:5173 ; API: http://127.0.0.1:8000'
} finally {
    Pop-Location
    $launchLock.Dispose()
}
