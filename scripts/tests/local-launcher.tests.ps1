# Free, deterministic tests. No real processes, database writes or HTTP calls.
$ErrorActionPreference = 'Stop'
$project = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot '..\..'))
$helpers = Join-Path $project 'scripts\local-infrastructure.ps1'
$devHelpers = Join-Path $project 'scripts\dev-processes.ps1'
$script:passed = 0
function Assert-True($Condition, [string]$Reason) {
    if (-not $Condition) { throw "Assertion failed: $Reason" }
}
function Assert-Throws([scriptblock]$Action, [string]$Pattern) {
    $caught = $null
    try { & $Action | Out-Null } catch { $caught = $_.Exception.Message }
    Assert-True ($caught -and $caught -match $Pattern) "Expected failure matching $Pattern; got $caught"
}
function Test-Case([string]$Name, [scriptblock]$Action) {
    & $Action
    $script:passed++
    Write-Host "PASS $Name"
}
function Test-Pg([string]$Variant, [scriptblock]$Check) {
    . $helpers
    $data = Join-Path $env:LOCALAPPDATA 'AILeadAutomationPro\postgres-17-demo-test\data'
    $expectedExe = Join-Path $project '.local\postgresql-17.11-4\pgsql\bin\postgres.exe'
    $created = [DateTimeOffset]::FromUnixTimeSeconds(1770000000).UtcDateTime
    $fixtureServer = [pscustomobject]@{ProcessId=101;ExecutablePath=$expectedExe;CommandLine="postgres -D `"$data`"";CreationDate=$created}
    $script:queries = 0
    function Test-Path { param($LiteralPath) return $Variant -ne 'absent' }
    function Get-Content {
        param($LiteralPath, [switch]$Raw)
        if ($LiteralPath.EndsWith('project-owner.json')) {
            return (@{projectRoot=$(if ($Variant -eq 'foreign-marker') {'C:\foreign'} else {$project});purpose='synthetic-demo-test'} | ConvertTo-Json)
        }
        return @('101',$data,'1770000000','15432')
    }
    function Get-NetTCPConnection {
        param($State, $LocalPort, $ErrorAction)
        if ($Variant -in @('absent','stopped','not-ready')) { return }
        [pscustomobject]@{OwningProcess=$(if ($Variant -eq 'foreign-port') {999} else {101});LocalAddress='127.0.0.1'}
    }
    function Get-CimInstance {
        param($ClassName, $Filter, $ErrorAction)
        if ($Variant -in @('absent','stopped')) { return }
        if ($Variant -eq 'foreign-exe') { $fixtureServer.ExecutablePath = 'C:\foreign\postgres.exe' }
        if ($Variant -eq 'reused-pid') { $fixtureServer.CreationDate = $created.AddMinutes(10) }
        return $fixtureServer
    }
    function Invoke-ProjectPgQuery {
        param($ProjectRoot, $Database, $Role, $Sql)
        $script:queries++
        if ($Variant -eq 'bad-db') { throw 'Expected demo database readiness failed' }
        if ($Database -eq 'postgres') {
            if ($Variant -eq 'bad-data') { return 'C:\foreign\data' }
            return $data
        }
        return 'ai_leads_demo|ai_leads_demo|15432'
    }
    function Start-Process { throw 'Tests must not start processes' }
    function Stop-Process { throw 'Tests must not stop processes' }
    & $Check
}
Test-Case 'free PostgreSQL port: no existing service; cold-start branch remains available' {
    Test-Pg absent { Assert-True ($null -eq (Get-OwnedPostgres $project -RequireReady)) 'cold start' }
}
Test-Case 'stale marker with no process: cold start allowed' {
    Test-Pg stopped { Assert-True ($null -eq (Get-OwnedPostgres $project)) 'stopped cluster' }
}
Test-Case 'owned PostgreSQL: process, data directory and demo database verified' {
    Test-Pg ready {
        Assert-True ((Get-OwnedPostgres $project -RequireReady).ProcessId -eq 101) 'owned process'
        Assert-True ($script:queries -eq 2) 'both data and DB connection checks'
    }
}
foreach ($scenario in @('foreign-port','foreign-exe','foreign-marker','reused-pid','bad-db','bad-data','not-ready')) {
    Test-Case "PostgreSQL rejects $scenario without starting or stopping a process" {
        Test-Pg $scenario { Assert-Throws { Get-OwnedPostgres $project -RequireReady } 'mismatch|unverified|readiness|not ready' }
    }
}
function Test-N8n([string]$Variant, [scriptblock]$Check) {
    . $devHelpers
    . $helpers
    $cli = Join-Path $env:LOCALAPPDATA 'AILeadAutomationPro\n8n-runtime\node_modules\n8n\bin\n8n'
    $created = [DateTime]::UtcNow
    function Test-Path { param($LiteralPath) return $true }
    function Get-Content {
        param($LiteralPath, [switch]$Raw)
        if ($LiteralPath.EndsWith('process.json')) { return (@{pid=201;cliPath=$cli;startedAt=$created.ToString('o')} | ConvertTo-Json) }
        if ($LiteralPath.Contains('n8n-runtime')) { return (@{projectRoot=$project;purpose='synthetic-demo-runtime'} | ConvertTo-Json) }
        return (@{root=$project;purpose='synthetic-demo'} | ConvertTo-Json)
    }
    function Get-Command { param($Name) return @{Source='C:\node\node.exe'} }
    function Get-CimInstance {
        param($ClassName, $Filter, $ErrorAction)
        [pscustomobject]@{ProcessId=201;Name='node.exe';ExecutablePath='C:\node\node.exe';CommandLine=$cli;CreationDate=$created}
    }
    function Get-NetTCPConnection {
        param($State, $LocalPort, $ErrorAction)
        [pscustomobject]@{OwningProcess=$(if ($Variant -eq 'foreign') {999} else {201})}
    }
    function Invoke-WebRequest { param($Uri, [switch]$UseBasicParsing, $TimeoutSec) return @{StatusCode=200} }
    function Start-Process { throw 'Duplicate start forbidden' }
    function Stop-Process { throw 'Unrelated stop forbidden' }
    function Start-Sleep { throw 'Ready service should not wait or restart' }
    & $Check
}
Test-Case 'owned ready n8n: reuse twice, no duplicate start' {
    Test-N8n ready {
        1..2 | ForEach-Object {
            $record = Get-OwnedN8nRecord $project
            Assert-True ($record.pid -eq 201) 'same n8n process'
            Wait-DevService $record 5681 'http://127.0.0.1:5681/healthz/readiness'
        }
    }
}
Test-Case 'n8n foreign listener: refusal without kill' {
    Test-N8n foreign { Assert-Throws { Get-OwnedN8nRecord $project } 'unverified' }
}
foreach ($service in @('api','frontend','worker')) {
    Test-Case "owned ${service}: identity and ready reuse" {
        . $devHelpers
        $created = [DateTime]::UtcNow
        $needle = @{api='app.main:create_app';frontend='node_modules/vite/bin/vite.js';worker='app.worker'}[$service]
        $record = @{name=$service;pid=301;path='C:\project\runtime.exe';startedAt=$created.ToString('o')}
        function Get-CimInstance {
            param($ClassName, $Filter, $ErrorAction)
            [pscustomobject]@{ProcessId=301;ExecutablePath=$record.path;CommandLine=$needle;CreationDate=$created}
        }
        function Get-NetTCPConnection { param($State, $LocalPort, $ErrorAction) [pscustomobject]@{OwningProcess=301} }
        function Invoke-WebRequest { param($Uri, [switch]$UseBasicParsing, $TimeoutSec) @{StatusCode=200} }
        function Start-Process { throw 'Duplicate start forbidden' }
        function Start-Sleep { throw 'Already ready' }
        1..2 | ForEach-Object {
            Assert-True ((Get-OwnedDevProcess $record).ProcessId -eq 301) 'identity'
            if ($service -ne 'worker') { Wait-DevService $record 8000 'http://127.0.0.1/' }
        }
    }
}
Test-Case 'STOP rejects unexpected child before stopping any process' {
    . $devHelpers
    $record = @{name='api';pid=401;path='C:\project\python.exe'}
    function Get-CimInstance {
        param($ClassName, $Filter, $ErrorAction)
        if ($Filter -like 'ParentProcessId=*') { return [pscustomobject]@{ProcessId=402;Name='foreign.exe';ExecutablePath='C:\foreign.exe';CommandLine='foreign'} }
        [pscustomobject]@{ProcessId=401;Name='python.exe';ExecutablePath=$record.path;CommandLine='app.main:create_app';CreationDate=[DateTime]::UtcNow}
    }
    function Stop-Process { throw 'No process may be stopped after failed preflight' }
    Assert-Throws { Stop-OwnedDevTree $record $project } 'Unexpected service child'
}
Test-Case 'unrecorded worker refuses duplicate without adopting or killing it' {
    . $devHelpers
    function Get-CimInstance { param($ClassName, $Filter, $ErrorAction) [pscustomobject]@{ProcessId=999;CommandLine='python -m app.worker'} }
    function Stop-Process { throw 'Unknown worker must not be stopped' }
    Assert-Throws { Assert-NoUnrecordedWorker } 'unrecorded app.worker'
}
Test-Case 'no worker: fresh launch is permitted' {
    . $devHelpers
    function Get-CimInstance { param($ClassName, $Filter, $ErrorAction) return }
    Assert-NoUnrecordedWorker
}
Test-Case 'STOP stops only the owned tree, preserving an unrelated process' {
    . $devHelpers
    $created = [DateTime]::UtcNow
    $record = @{name='api';pid=401;path='C:\project\python.exe';startedAt=$created.ToString('o')}
    $script:stopped = @()
    function Get-CimInstance {
        param($ClassName, $Filter, $ErrorAction)
        if ($Filter -eq 'ParentProcessId=401') {
            if ($script:stopped -notcontains 402) { return [pscustomobject]@{ProcessId=402;Name='python.exe';ExecutablePath=$record.path;CommandLine='app.main:create_app';CreationDate=$created} }
            return
        }
        if ($Filter -like 'ParentProcessId=*') { return }
        $target = [int]($Filter.Split('=')[1])
        if ($script:stopped -contains $target) { return }
        [pscustomobject]@{ProcessId=$target;Name='python.exe';ExecutablePath=$record.path;CommandLine='app.main:create_app';CreationDate=$created}
    }
    function Stop-Process { param($Id, $ErrorAction) $script:stopped += $Id }
    Stop-OwnedDevTree $record $project
    Assert-True (($script:stopped -join ',') -eq '402,401') 'only owned child and parent, in safe order'
    Assert-True ((Get-CimInstance Win32_Process -Filter 'ProcessId=999').ProcessId -eq 999) 'unrelated process remains'
}
Test-Case 'launch lock prevents concurrent START/STOP ownership changes' {
    . $devHelpers
    $temporaryRoot = Join-Path ([IO.Path]::GetTempPath()) ([Guid]::NewGuid().ToString('N'))
    $directory = New-Item -ItemType Directory -Path (Join-Path $temporaryRoot '.local')
    $lock = Enter-ProjectLaunchLock $temporaryRoot
    try {
        Assert-Throws { [IO.File]::Open((Join-Path $directory.FullName 'dev-launch.lock'), 'OpenOrCreate', 'ReadWrite', 'None') } 'another process|being used'
    } finally {
        $lock.Dispose()
        Remove-Item -LiteralPath (Join-Path $directory.FullName 'dev-launch.lock')
        Remove-Item -LiteralPath $directory.FullName
        Remove-Item -LiteralPath $temporaryRoot
    }
}
Test-Case 'PowerShell syntax for every changed script' {
    $paths = @('dev-processes','local-infrastructure','start-dev','stop-dev','start-postgres','stop-postgres','start-n8n','stop-n8n')
    foreach ($name in $paths) {
        $parseTokens = $null; $parseErrors = $null
        [System.Management.Automation.Language.Parser]::ParseFile((Join-Path $project "scripts\$name.ps1"), [ref]$parseTokens, [ref]$parseErrors) | Out-Null
        Assert-True (-not $parseErrors.Count) "$name syntax"
    }
}
Test-Case 'CMD entry points quote their root and use existing lifecycle scripts' {
    $start = Get-Content -LiteralPath (Join-Path $project 'START_FLOWBRIDGE.cmd') -Raw
    $stop = Get-Content -LiteralPath (Join-Path $project 'STOP_FLOWBRIDGE.cmd') -Raw
    Assert-True ($start.Contains('"%~dp0scripts\start-dev.ps1" -OpenBrowser')) 'START target'
    Assert-True ($stop.Contains('"%~dp0scripts\stop-dev.ps1" -IncludeInfrastructure')) 'STOP target'
    Assert-True ($start.Contains('exit /b %flowbridge_exit%') -and $stop.Contains('exit /b %flowbridge_exit%')) 'exit codes'
}
Test-Case 'demo n8n readiness timeout remains bounded before any startup' {
    foreach ($invalid in @(0,601)) {
        Assert-Throws { & (Join-Path $project 'scripts\start-n8n.ps1') -TimeoutSeconds $invalid } 'TimeoutSeconds'
    }
}
Write-Host "$script:passed launcher regression checks passed; PowerShell $($PSVersionTable.PSVersion)."
