# Ownership checks only. Dot-sourcing this file has no service side effects.
function Invoke-ProjectPgQuery([string]$ProjectRoot, [string]$Database, [string]$Role, [string]$Sql) {
    $psql = Join-Path $ProjectRoot '.local\postgresql-17.11-4\pgsql\bin\psql.exe'
    $connection = "host=127.0.0.1 port=15432 dbname=$Database user=$Role connect_timeout=3"
    $result = & $psql -X -w -At -v ON_ERROR_STOP=1 -d $connection -c $Sql 2>$null
    if ($LASTEXITCODE -ne 0) { throw 'Project PostgreSQL connection/readiness verification failed; no process was changed.' }
    return $result
}

function Get-OwnedPostgres([string]$ProjectRoot, [switch]$RequireReady) {
    $runtime = Join-Path $env:LOCALAPPDATA 'AILeadAutomationPro\postgres-17-demo-test'
    $data = Join-Path $runtime 'data'
    $marker = Join-Path $runtime 'project-owner.json'
    $listeners = @(Get-NetTCPConnection -State Listen -LocalPort 15432 -ErrorAction SilentlyContinue)
    if (Test-Path -LiteralPath $marker) {
        $owner = Get-Content -LiteralPath $marker -Raw | ConvertFrom-Json
        if ($owner.projectRoot -ne $ProjectRoot -or $owner.purpose -ne 'synthetic-demo-test') {
            throw 'PostgreSQL ownership mismatch; no process was changed.'
        }
    } elseif ($listeners.Count -or (Test-Path -LiteralPath $runtime)) {
        throw 'PostgreSQL ownership marker is missing; no process was changed.'
    } else { return $null }
    $pidFile = Join-Path $data 'postmaster.pid'
    $server = $null
    if (Test-Path -LiteralPath $pidFile) {
        $lines = @(Get-Content -LiteralPath $pidFile)
        $serverPid = 0
        if ($lines.Count -lt 4 -or -not [int]::TryParse($lines[0], [ref]$serverPid) -or $serverPid -le 0) {
            throw 'Invalid PostgreSQL process marker; no process was changed.'
        }
        $server = Get-CimInstance Win32_Process -Filter "ProcessId=$serverPid" -ErrorAction SilentlyContinue
        if ($server) {
            $expectedExe = Join-Path $ProjectRoot '.local\postgresql-17.11-4\pgsql\bin\postgres.exe'
            $command = ([string]$server.CommandLine).Replace('/', '\')
            $started = [DateTimeOffset]::FromUnixTimeSeconds([long]$lines[2]).UtcDateTime
            if ($server.ExecutablePath -ne $expectedExe -or
                -not $command.Contains(('"' + $data + '"')) -or
                $lines[1].Replace('/', '\') -ne $data -or $lines[3] -ne '15432' -or
                [Math]::Abs((([DateTime]$server.CreationDate).ToUniversalTime() - $started).TotalSeconds) -gt 2) {
                throw 'PostgreSQL process identity/data directory mismatch; no process was changed.'
            }
        }
    }
    foreach ($listener in $listeners) {
        if (-not $server -or [int]$listener.OwningProcess -ne [int]$server.ProcessId -or $listener.LocalAddress -ne '127.0.0.1') {
            throw 'Port 15432 belongs to an unverified process; no process was changed.'
        }
    }
    if (-not $server) { return $null }
    if ($RequireReady) {
        if (-not $listeners.Count) { throw 'Owned PostgreSQL is running but not ready; no duplicate was started.' }
        $actualData = Invoke-ProjectPgQuery $ProjectRoot 'postgres' 'ai_leads_admin' 'SHOW data_directory;'
        if (([string]$actualData).Replace('/', '\') -ne $data) { throw 'Connected PostgreSQL data directory mismatch.' }
        $identity = Invoke-ProjectPgQuery $ProjectRoot 'ai_leads_demo' 'ai_leads_demo' 'SELECT current_database(), current_user, inet_server_port();'
        if ($identity -ne 'ai_leads_demo|ai_leads_demo|15432') { throw 'Expected demo database readiness failed.' }
    }
    return $server
}

function Get-OwnedN8nRecord([string]$ProjectRoot, [string]$Profile='demo') {
    $runtime = Join-Path $env:LOCALAPPDATA "AILeadAutomationPro\n8n-$Profile"
    $port = if ($Profile -eq 'demo') { 5681 } else { 5683 }
    $listeners = @(Get-NetTCPConnection -State Listen -LocalPort $port -ErrorAction SilentlyContinue)
    $marker = Join-Path $runtime 'project-owner.json'
    if (-not (Test-Path -LiteralPath $marker)) {
        if ($listeners.Count -or (Test-Path -LiteralPath $runtime)) { throw 'n8n ownership marker is missing; no process was changed.' }
        return $null
    }
    $owner = Get-Content -LiteralPath $marker -Raw | ConvertFrom-Json
    if ($owner.root -ne $ProjectRoot -or $owner.purpose -ne "synthetic-$Profile") { throw 'n8n belongs to another project.' }
    $sharedRuntime = Join-Path $env:LOCALAPPDATA 'AILeadAutomationPro\n8n-runtime'
    $runtimeOwner = Get-Content -LiteralPath (Join-Path $sharedRuntime 'project-owner.json') -Raw | ConvertFrom-Json
    if ($runtimeOwner.projectRoot -ne $ProjectRoot -or $runtimeOwner.purpose -ne 'synthetic-demo-runtime') { throw 'n8n runtime ownership mismatch.' }
    $recordPath = Join-Path $runtime 'process.json'
    $record = $null
    if (Test-Path -LiteralPath $recordPath) {
        $saved = Get-Content -LiteralPath $recordPath -Raw | ConvertFrom-Json
        if ($saved.cliPath -ne (Join-Path $sharedRuntime 'node_modules\n8n\bin\n8n') -or -not $saved.startedAt) { throw 'Unexpected n8n process record.' }
        $record = @{name='n8n';pid=$saved.pid;path=(Get-Command node.exe).Source;startedAt=$saved.startedAt}
        if (-not (Get-OwnedDevProcess $record)) { $record = $null }
    }
    foreach ($listener in $listeners) {
        if (-not $record -or -not (Test-ListenerBelongsTo $record $listener.OwningProcess)) { throw "Port $port belongs to an unverified process; it was not changed." }
    }
    return $record
}
