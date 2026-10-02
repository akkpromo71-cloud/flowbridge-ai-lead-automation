# Shared lifecycle helpers. Loading this file does not start or stop processes.
function Enter-ProjectLaunchLock([string]$ProjectRoot) {
    $lockPath = Join-Path $ProjectRoot '.local\dev-launch.lock'
    $deadline = [DateTime]::UtcNow.AddSeconds(30)
    do {
        try { return [IO.File]::Open($lockPath, [IO.FileMode]::OpenOrCreate, [IO.FileAccess]::ReadWrite, [IO.FileShare]::None) }
        catch [IO.IOException] { Start-Sleep -Milliseconds 200 }
    } while ([DateTime]::UtcNow -lt $deadline)
    throw 'Another project start/restart is still running; no duplicate process was created.'
}

function Get-OwnedDevProcess($Record) {
    $needles = @{ api='app.main:create_app'; frontend='node_modules/vite/bin/vite.js'; worker='app.worker' }
    if (-not $Record -or $Record.name -notin $needles.Keys -or -not $Record.pid -or -not $Record.path) {
        throw 'Invalid project process record.'
    }
    $current = Get-CimInstance Win32_Process -Filter "ProcessId=$($Record.pid)" -ErrorAction SilentlyContinue
    if (-not $current) { return $null }
    if ($current.ExecutablePath -ne $Record.path -or -not $current.CommandLine.Contains($needles[$Record.name])) {
        throw "PID $($Record.pid) no longer belongs to this recorded service; it was not changed."
    }
    if ($Record.startedAt -and [Math]::Abs((([DateTime]$current.CreationDate).ToUniversalTime() - ([DateTime]$Record.startedAt).ToUniversalTime()).TotalSeconds) -gt 2) {
        throw 'Process identity does not match the saved start time.'
    }
    return $current
}

function Get-LiveDevRecords([string]$Marker) {
    if (-not (Test-Path -LiteralPath $Marker)) { return @() }
    $records = Get-Content -LiteralPath $Marker -Raw | ConvertFrom-Json
    $seen = @{}
    foreach ($record in $records) {
        if (Get-OwnedDevProcess $record) {
            if (-not $seen.ContainsKey([int]$record.pid)) { $seen[[int]$record.pid]=$true; $record }
        }
    }
}

function Save-DevRecords([string]$Marker, [array]$Records) {
    $temporary = "$Marker.$([Guid]::NewGuid().ToString('N')).tmp"
    [IO.File]::WriteAllText($temporary, (ConvertTo-Json -InputObject @($Records) -Depth 5))
    Move-Item -LiteralPath $temporary -Destination $Marker -Force
}

function Test-ListenerBelongsTo($Record, [int]$ListenerPid) {
    $candidate = $ListenerPid
    for ($depth=0; $depth -lt 4; $depth++) {
        if ($candidate -eq [int]$Record.pid) { return $true }
        $process = Get-CimInstance Win32_Process -Filter "ProcessId=$candidate" -ErrorAction SilentlyContinue
        if (-not $process) { return $false }
        $candidate = [int]$process.ParentProcessId
    }
    return $false
}

function Wait-DevService($Record, [int]$Port, [string]$Url, [int]$TimeoutSeconds=30) {
    $deadline = [DateTime]::UtcNow.AddSeconds($TimeoutSeconds)
    do {
        if (-not (Get-OwnedDevProcess $Record)) { throw "$($Record.name) exited before readiness; inspect its .local logs." }
        $listeners = @(Get-NetTCPConnection -State Listen -LocalPort $Port -ErrorAction SilentlyContinue)
        foreach ($listener in $listeners) {
            if (-not (Test-ListenerBelongsTo $Record $listener.OwningProcess)) {
                throw "Port $Port belongs to a different process; it was not changed."
            }
        }
        if ($listeners.Count) {
            try {
                $response = Invoke-WebRequest -Uri $Url -UseBasicParsing -TimeoutSec 2
                if ($response.StatusCode -eq 200) { return }
            } catch { # Keep waiting only while the owned process is alive.
            }
        }
        Start-Sleep -Milliseconds 250
    } while ([DateTime]::UtcNow -lt $deadline)
    throw "$($Record.name) did not become ready within $TimeoutSeconds seconds; inspect its .local logs."
}

function Get-OwnedDevTree($Record, [string]$ProjectRoot) {
    $owned = Get-OwnedDevProcess $Record
    if (-not $owned) { return }
    $needles = @{ api='app.main:create_app'; worker='app.worker' }
    $expectedEsbuild = Join-Path $ProjectRoot 'frontend\node_modules\@esbuild\win32-x64\esbuild.exe'
    $expectedConsole = Join-Path $env:SystemRoot 'System32\conhost.exe'
    $tree = @($owned)
    for ($index=0; $index -lt $tree.Count; $index++) {
        if ($tree.Count -gt 12) { throw 'Unexpected process tree size; no process was stopped.' }
        $children = @(Get-CimInstance Win32_Process -Filter "ParentProcessId=$($tree[$index].ProcessId)" -ErrorAction SilentlyContinue)
        foreach ($child in $children) {
            $console = $child.Name -eq 'conhost.exe' -and $child.ExecutablePath -eq $expectedConsole
            $python = $Record.name -in @('api','worker') -and $child.Name -eq 'python.exe' -and $child.CommandLine.Contains($needles[$Record.name])
            $esbuild = $Record.name -eq 'frontend' -and $child.Name -eq 'esbuild.exe' -and $child.ExecutablePath -eq $expectedEsbuild
            if (-not ($console -or $python -or $esbuild)) { throw 'Unexpected service child; no unrelated process was stopped.' }
            $tree += $child
        }
    }
    return $tree
}

function Stop-OwnedDevTree($Record, [string]$ProjectRoot) {
    $tree = @(Get-OwnedDevTree $Record $ProjectRoot)
    # Reverse order handles the venv launcher, its Python child and Vite/esbuild.
    [array]::Reverse($tree)
    foreach ($saved in $tree) {
        $current = Get-CimInstance Win32_Process -Filter "ProcessId=$($saved.ProcessId)" -ErrorAction SilentlyContinue
        if (-not $current) { continue }
        if ($current.CreationDate -ne $saved.CreationDate -or $current.ExecutablePath -ne $saved.ExecutablePath) {
            throw 'A process identity changed during shutdown; the reused PID was not stopped.'
        }
        Stop-Process -Id $current.ProcessId -ErrorAction SilentlyContinue
    }
    $deadline = [DateTime]::UtcNow.AddSeconds(10)
    do {
        $alive = @($tree | Where-Object {
            $current = Get-CimInstance Win32_Process -Filter "ProcessId=$($_.ProcessId)" -ErrorAction SilentlyContinue
            $current -and $current.CreationDate -eq $_.CreationDate
        })
        if (-not $alive.Count) { return }
        Start-Sleep -Milliseconds 200
    } while ([DateTime]::UtcNow -lt $deadline)
    throw 'An owned service process did not stop within 10 seconds.'
}
