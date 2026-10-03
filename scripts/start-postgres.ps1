[CmdletBinding()]
param()

# Project-local binaries; synthetic demo/test data outside the OneDrive workspace.
# This script never installs a Windows service or changes PATH or firewall rules.
$ErrorActionPreference = 'Stop'
$projectRoot = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot '..'))
$localRoot = Join-Path $projectRoot '.local'
$packageRoot = Join-Path $localRoot 'postgresql-17.11-4'
$binRoot = Join-Path $packageRoot 'pgsql\bin'
$runtimeRoot = Join-Path $env:LOCALAPPDATA 'AILeadAutomationPro\postgres-17-demo-test'
$dataRoot = Join-Path $runtimeRoot 'data'
$markerPath = Join-Path $runtimeRoot 'project-owner.json'
$port = 15432
$archiveName = 'postgresql-17.11-4-windows-x64-binaries.zip'
$archivePath = Join-Path (Join-Path $localRoot 'downloads') $archiveName
$archiveUrl = "https://get.enterprisedb.com/postgresql/$archiveName"
$expectedHash = 'B9424EE7BC60B52450FF910A3630225DF32E633F3CB29C1D126D9299D59AEA28'
. (Join-Path $PSScriptRoot 'local-infrastructure.ps1')
if (Get-OwnedPostgres $projectRoot -RequireReady) {
    Write-Host 'PostgreSQL: already running'
    return
}

if (Test-Path -LiteralPath $runtimeRoot) {
    if (-not (Test-Path -LiteralPath $markerPath)) {
        throw "Refusing to use an unowned directory: $runtimeRoot"
    }
    $owner = Get-Content -LiteralPath $markerPath -Raw | ConvertFrom-Json
    if ($owner.projectRoot -ne $projectRoot -or $owner.purpose -ne 'synthetic-demo-test') {
        throw 'This PostgreSQL data directory belongs to a different project.'
    }
} else {
    New-Item -ItemType Directory -Path $runtimeRoot -Force | Out-Null
    @{ projectRoot = $projectRoot; purpose = 'synthetic-demo-test' } |
        ConvertTo-Json | Set-Content -LiteralPath $markerPath -Encoding UTF8
}

if (-not (Test-Path -LiteralPath (Join-Path $binRoot 'postgres.exe'))) {
    New-Item -ItemType Directory -Path (Split-Path $archivePath) -Force | Out-Null
    if (-not (Test-Path -LiteralPath $archivePath)) {
        Write-Host 'Downloading the pinned PostgreSQL portable archive from EDB...'
        & curl.exe --fail --location --silent --show-error $archiveUrl --output $archivePath
        if ($LASTEXITCODE -ne 0) { throw 'PostgreSQL download failed; no server was started.' }
    }
    if ((Get-FileHash -Algorithm SHA256 -LiteralPath $archivePath).Hash -ne $expectedHash) {
        throw 'PostgreSQL archive checksum does not match the pinned EDB SHA-256.'
    }
    # Extract only the database and CLI runtime, including its license notices.
    # pgAdmin and StackBuilder are not needed and are not executed.
    Add-Type -AssemblyName System.IO.Compression.FileSystem
    $archive = [IO.Compression.ZipFile]::OpenRead($archivePath)
    try {
        $allowedRoot = [IO.Path]::GetFullPath($packageRoot) + [IO.Path]::DirectorySeparatorChar
        foreach ($entry in $archive.Entries) {
            if ($entry.FullName -notmatch '^pgsql/(bin/|lib/|share/|[^/]*license[^/]*$)') { continue }
            $destination = [IO.Path]::GetFullPath((Join-Path $packageRoot $entry.FullName))
            if (-not $destination.StartsWith($allowedRoot, [StringComparison]::OrdinalIgnoreCase)) {
                throw 'Unsafe path encountered in PostgreSQL archive.'
            }
            if ($entry.Name -eq '') {
                New-Item -ItemType Directory -Path $destination -Force | Out-Null
            } else {
                New-Item -ItemType Directory -Path (Split-Path $destination) -Force | Out-Null
                [IO.Compression.ZipFileExtensions]::ExtractToFile($entry, $destination, $true)
            }
        }
    } finally { $archive.Dispose() }
}

$pgCtl = Join-Path $binRoot 'pg_ctl.exe'
$psql = Join-Path $binRoot 'psql.exe'
& (Join-Path $binRoot 'postgres.exe') --version
if ($LASTEXITCODE -ne 0) { throw 'PostgreSQL runtime could not load on this computer.' }

$running = $false
if (Test-Path -LiteralPath (Join-Path $dataRoot 'PG_VERSION')) {
    & $pgCtl status -D $dataRoot *> $null
    $running = $LASTEXITCODE -eq 0
}
if (-not $running) {
    $listener = Get-NetTCPConnection -State Listen -LocalPort $port -ErrorAction SilentlyContinue
    if ($listener) { throw "Port $port is occupied; no existing process was changed." }
    if (-not (Test-Path -LiteralPath (Join-Path $dataRoot 'PG_VERSION'))) {
        & (Join-Path $binRoot 'initdb.exe') -D $dataRoot -U ai_leads_admin --encoding=UTF8 --locale=C --auth=reject
        if ($LASTEXITCODE -ne 0) { throw 'PostgreSQL initialization failed.' }
        @"
listen_addresses = '127.0.0.1'
port = $port
max_connections = 40
shared_buffers = '64MB'
timezone = 'UTC'
log_statement = 'none'
log_min_error_statement = 'panic'
"@ | Add-Content -LiteralPath (Join-Path $dataRoot 'postgresql.conf') -Encoding ASCII
        @'
# SYNTHETIC DEMO/TEST ONLY. No live credentials or customer data are permitted.
# This server binds only IPv4 loopback. Trust is restricted to named local roles.
host all           ai_leads_admin 127.0.0.1/32 trust
host ai_leads_demo ai_leads_demo  127.0.0.1/32 trust
host ai_leads_test ai_leads_test  127.0.0.1/32 trust
host ai_leads_n8n  ai_leads_n8n   127.0.0.1/32 trust
host all           all           0.0.0.0/0    reject
host all           all           ::/0         reject
'@ | Set-Content -LiteralPath (Join-Path $dataRoot 'pg_hba.conf') -Encoding ASCII
    }
    $logPath = Join-Path $runtimeRoot 'postgres.log'
    $arguments = @('start', '-D', ('"' + $dataRoot + '"'), '-l', ('"' + $logPath + '"'), '-w', '-t', '30')
    $launcher = Start-Process -FilePath $pgCtl -ArgumentList $arguments -WindowStyle Hidden -PassThru
    # PowerShell -Wait follows the whole child tree, including the server itself.
    # Wait only for pg_ctl, which exits once PostgreSQL is ready.
    if (-not $launcher.WaitForExit(35000)) { throw 'PostgreSQL readiness check timed out.' }
    $launcher.Refresh()
    if ($launcher.ExitCode -ne 0) { throw "PostgreSQL start failed. See $logPath" }
}

& (Join-Path $binRoot 'pg_isready.exe') -h 127.0.0.1 -p $port -U ai_leads_admin -d postgres
if ($LASTEXITCODE -ne 0) { throw 'PostgreSQL is not accepting connections.' }

# Upgrade only this marker-verified synthetic cluster. Controlled smoke is kept
# in separate restricted roles/databases; no application key reaches PostgreSQL.
$hbaPath = Join-Path $dataRoot 'pg_hba.conf'
$hbaText = Get-Content -LiteralPath $hbaPath -Raw
if ($hbaText -notmatch 'host ai_leads_controlled ai_leads_controlled') {
    $controlledRules = "host ai_leads_controlled ai_leads_controlled 127.0.0.1/32 trust`nhost ai_leads_n8n_controlled ai_leads_n8n_controlled 127.0.0.1/32 trust`n"
    Set-Content -LiteralPath $hbaPath -Value ($controlledRules + $hbaText) -Encoding ASCII
    & $pgCtl reload -D $dataRoot
    if ($LASTEXITCODE -ne 0) { throw 'Project-local PostgreSQL reload failed.' }
}

$bootstrapSql = @'
DO $$ BEGIN
 IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'ai_leads_demo') THEN
  CREATE ROLE ai_leads_demo LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION;
 END IF;
 IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'ai_leads_test') THEN
  CREATE ROLE ai_leads_test LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION;
 END IF;
 IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'ai_leads_n8n') THEN
  CREATE ROLE ai_leads_n8n LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION;
 END IF;
 IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'ai_leads_controlled') THEN
  CREATE ROLE ai_leads_controlled LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION;
 END IF;
 IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'ai_leads_n8n_controlled') THEN
  CREATE ROLE ai_leads_n8n_controlled LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION;
 END IF;
END $$;
SELECT 'CREATE DATABASE ai_leads_demo OWNER ai_leads_demo ENCODING ''UTF8'''
 WHERE NOT EXISTS (SELECT FROM pg_database WHERE datname = 'ai_leads_demo') \gexec
SELECT 'CREATE DATABASE ai_leads_test OWNER ai_leads_test ENCODING ''UTF8'''
 WHERE NOT EXISTS (SELECT FROM pg_database WHERE datname = 'ai_leads_test') \gexec
SELECT 'CREATE DATABASE ai_leads_n8n OWNER ai_leads_n8n ENCODING ''UTF8'''
 WHERE NOT EXISTS (SELECT FROM pg_database WHERE datname = 'ai_leads_n8n') \gexec
SELECT 'CREATE DATABASE ai_leads_controlled OWNER ai_leads_controlled ENCODING ''UTF8'''
 WHERE NOT EXISTS (SELECT FROM pg_database WHERE datname = 'ai_leads_controlled') \gexec
SELECT 'CREATE DATABASE ai_leads_n8n_controlled OWNER ai_leads_n8n_controlled ENCODING ''UTF8'''
 WHERE NOT EXISTS (SELECT FROM pg_database WHERE datname = 'ai_leads_n8n_controlled') \gexec
REVOKE ALL ON DATABASE ai_leads_demo FROM PUBLIC;
REVOKE ALL ON DATABASE ai_leads_test FROM PUBLIC;
REVOKE ALL ON DATABASE ai_leads_n8n FROM PUBLIC;
GRANT CONNECT, TEMPORARY ON DATABASE ai_leads_demo TO ai_leads_demo;
GRANT CONNECT, TEMPORARY ON DATABASE ai_leads_test TO ai_leads_test;
GRANT CONNECT, TEMPORARY ON DATABASE ai_leads_n8n TO ai_leads_n8n;
REVOKE ALL ON DATABASE ai_leads_controlled FROM PUBLIC;
REVOKE ALL ON DATABASE ai_leads_n8n_controlled FROM PUBLIC;
GRANT CONNECT, TEMPORARY ON DATABASE ai_leads_controlled TO ai_leads_controlled;
GRANT CONNECT, TEMPORARY ON DATABASE ai_leads_n8n_controlled TO ai_leads_n8n_controlled;
'@
$bootstrapSql | & $psql -X -v ON_ERROR_STOP=1 -h 127.0.0.1 -p $port -U ai_leads_admin -d postgres
if ($LASTEXITCODE -ne 0) { throw 'Demo/test database setup failed.' }
foreach ($database in @('ai_leads_demo', 'ai_leads_test', 'ai_leads_n8n', 'ai_leads_controlled', 'ai_leads_n8n_controlled')) {
    & $psql -X -v ON_ERROR_STOP=1 -h 127.0.0.1 -p $port -U $database -d $database -c 'SELECT current_database(), current_user;'
    if ($LASTEXITCODE -ne 0) { throw "Connection check failed for $database." }
}
Write-Host 'Synthetic demo/test PostgreSQL is ready at 127.0.0.1:15432.'
Get-OwnedPostgres $projectRoot -RequireReady | Out-Null
Write-Host "Data and logs: $runtimeRoot"
