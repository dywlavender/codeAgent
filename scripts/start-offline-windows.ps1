[CmdletBinding()]
param(
    [string]$ProjectConfig,
    [string]$Database,
    [string]$HostAddress = "127.0.0.1",
    [ValidateRange(0, 65535)]
    [int]$Port = 0,
    [ValidateSet("model", "markdown")]
    [string]$BaselineParser = "model",
    [switch]$NoBrowser
)

$ErrorActionPreference = "Stop"
$ProgressPreference = "SilentlyContinue"
$ProjectRoot = Split-Path -Parent $PSScriptRoot
Set-Location $ProjectRoot

function Invoke-Checked {
    param([string]$Command, [string[]]$Arguments)
    & $Command @Arguments
    if ($LASTEXITCODE -ne 0) {
        throw "Command failed ($LASTEXITCODE): $Command $($Arguments -join ' ')"
    }
}

function Resolve-PythonLauncher {
    param([string]$RequiredVersion)
    if (Get-Command py.exe -ErrorAction SilentlyContinue) {
        $prefix = if ([string]::IsNullOrWhiteSpace($RequiredVersion)) { @("-3") } else { @("-$RequiredVersion") }
        return @{ Command = "py.exe"; Prefix = $prefix }
    }
    if (Get-Command python.exe -ErrorAction SilentlyContinue) {
        return @{ Command = "python.exe"; Prefix = @() }
    }
    throw "Python was not found. Install the exact version recorded in offline\package-info.json."
}

$runtime = Join-Path $PSScriptRoot "offline_runtime.py"
$metadata = Join-Path $ProjectRoot "offline\package-info.json"
$packageInfo = Get-Content $metadata -Raw | ConvertFrom-Json
$launcher = Resolve-PythonLauncher ([string]$packageInfo.pythonMajorMinor)
$repositoryMode = [string]$packageInfo.repositoryMode
$dependencyMode = [string]$packageInfo.dependencyMode
Invoke-Checked $launcher.Command (@($launcher.Prefix) + @($runtime, "validate", "--metadata", $metadata, "--target", "windows"))
if ($repositoryMode -eq "intranet-git" -and -not (Get-Command git.exe -ErrorAction SilentlyContinue)) {
    throw "Git was not found. Intranet Git mode requires Git on the deployment machine."
}

if ([string]::IsNullOrWhiteSpace($ProjectConfig)) {
    $defaultConfig = Join-Path $ProjectRoot "project.config.json"
    if (Test-Path -PathType Leaf $defaultConfig) {
        $ProjectConfig = $defaultConfig
    }
    else {
        throw "project.config.json was not found. Copy project.config.example.json and configure the intranet Git repositories."
    }
}

$baselineExists = $false
$ProjectConfig = [System.IO.Path]::GetFullPath($ProjectConfig)
if (-not (Test-Path -PathType Leaf $ProjectConfig)) { throw "Project config does not exist: $ProjectConfig" }
$defaultArguments = @($launcher.Prefix) + @($runtime, "defaults", "--config", $ProjectConfig)
$defaultsJson = & $launcher.Command @defaultArguments
if ($LASTEXITCODE -ne 0) { throw "Project config cannot be read: $ProjectConfig" }
$defaults = $defaultsJson | ConvertFrom-Json
if ([string]::IsNullOrWhiteSpace($Database)) { $Database = [string]$defaults.database }
if ($Port -eq 0) { $Port = [int]$defaults.port }
$baselineExists = [bool]$defaults.baselineExists

$VenvRoot = Join-Path $ProjectRoot ".venv-offline"
$VenvPython = Join-Path $VenvRoot "Scripts\python.exe"
if (-not (Test-Path -PathType Leaf $VenvPython)) {
    Write-Host "`n==> Creating offline Python environment" -ForegroundColor Cyan
    Invoke-Checked $launcher.Command (@($launcher.Prefix) + @("-m", "venv", $VenvRoot))
}

$requirementsFile = Join-Path $ProjectRoot "offline\requirements.txt"
$installedRequirements = Join-Path $VenvRoot ".requirements-installed.txt"
$dependenciesChanged = -not (Test-Path -PathType Leaf $installedRequirements)
if (-not $dependenciesChanged) {
    $dependenciesChanged = (Get-Content $requirementsFile -Raw) -ne (Get-Content $installedRequirements -Raw)
}
if ($dependenciesChanged) {
    if ($dependencyMode -eq "bundled-wheels") {
        Write-Host "`n==> Installing dependencies from bundled wheelhouse" -ForegroundColor Cyan
        Invoke-Checked $VenvPython @(
            "-m", "pip", "install", "--disable-pip-version-check", "--no-index",
            "--find-links", (Join-Path $ProjectRoot "offline\wheelhouse"),
            "-r", $requirementsFile
        )
    }
    else {
        Write-Host "`n==> Installing dependencies from configured Python package index" -ForegroundColor Cyan
        Invoke-Checked $VenvPython @(
            "-m", "pip", "install", "--disable-pip-version-check", "-r", $requirementsFile
        )
    }
    Copy-Item $requirementsFile $installedRequirements -Force
}

function Test-PortOpen([string]$Address, [int]$TargetPort) {
    $probeAddress = if ($Address -eq "0.0.0.0" -or $Address -eq "::") { "127.0.0.1" } else { $Address }
    $client = [System.Net.Sockets.TcpClient]::new()
    try {
        $async = $client.BeginConnect($probeAddress, $TargetPort, $null, $null)
        if ($async.AsyncWaitHandle.WaitOne(300) -and $client.Connected) { return $true }
        return $false
    }
    catch { return $false }
    finally { $client.Dispose() }
}

function Get-PortOwnerIds([int]$TargetPort) {
    try {
        return @(Get-NetTCPConnection -State Listen -LocalPort $TargetPort -ErrorAction SilentlyContinue |
            Select-Object -ExpandProperty OwningProcess -Unique)
    }
    catch { return @() }
}

function Stop-PortProcesses([string]$Address, [int]$TargetPort) {
    $ownerIds = @(Get-PortOwnerIds $TargetPort)
    if ($ownerIds.Count -eq 0) {
        throw "Port $TargetPort is busy, but its owning PID could not be determined."
    }
    foreach ($ownerId in $ownerIds) {
        Write-Host "`n==> Stopping process listening on port $TargetPort (PID $ownerId)" -ForegroundColor Cyan
        Stop-Process -Id ([int]$ownerId) -Force -ErrorAction SilentlyContinue
    }
    for ($attempt = 0; $attempt -lt 20; $attempt++) {
        if (-not (Test-PortOpen $Address $TargetPort)) { return }
        Start-Sleep -Milliseconds 250
    }
    throw "Port $TargetPort is still in use after stopping its processes."
}

$DatabasePath = if ([System.IO.Path]::IsPathRooted($Database)) { $Database } else { Join-Path $ProjectRoot $Database }
$DatabasePath = [System.IO.Path]::GetFullPath($DatabasePath)
New-Item -ItemType Directory -Force -Path (Split-Path -Parent $DatabasePath) | Out-Null

Write-Host "`n==> Preparing offline knowledge database" -ForegroundColor Cyan
Invoke-Checked $VenvPython @("-m", "business_code_agent.cli", "init-db", "--db", $DatabasePath)
$syncArguments = @("-m", "business_code_agent.cli", "sync-project", "--config", $ProjectConfig, "--db", $DatabasePath)
if ($repositoryMode -eq "bundled-snapshot") { $syncArguments += "--offline" }
Invoke-Checked $VenvPython $syncArguments
if ($baselineExists) {
    $baselineArguments = @("-m", "business_code_agent.cli", "baseline-refresh", "--config", $ProjectConfig, "--db", $DatabasePath, "--parser", $BaselineParser)
    Invoke-Checked $VenvPython $baselineArguments
}

if (Test-PortOpen $HostAddress $Port) {
    Write-Host "`n==> Port $Port is already in use; stopping the existing process and restarting" -ForegroundColor Cyan
    Stop-PortProcesses $HostAddress $Port
}
if (Test-PortOpen $HostAddress $Port) {
    throw "Port $Port is still in use."
}
$urlHost = if ($HostAddress -eq "0.0.0.0" -or $HostAddress -eq "::") { "127.0.0.1" } else { $HostAddress }
$url = "http://$urlHost`:$Port/"
$logPath = Join-Path (Split-Path -Parent $DatabasePath) "server.log"
$errorLogPath = Join-Path (Split-Path -Parent $DatabasePath) "server-error.log"
$serverArguments = @("-m", "business_code_agent.cli", "serve-query", "--db", ('"' + $DatabasePath + '"'), "--host", $HostAddress, "--port", "$Port", "--project-config", ('"' + $ProjectConfig + '"'))

$server = Start-Process -FilePath $VenvPython -ArgumentList $serverArguments -PassThru -WindowStyle Hidden -RedirectStandardOutput $logPath -RedirectStandardError $errorLogPath
try {
    $ready = $false
    for ($attempt = 0; $attempt -lt 40; $attempt++) {
        if ($server.HasExited) {
            $detail = if (Test-Path $errorLogPath) { Get-Content $errorLogPath -Raw } else { "No error log was written." }
            throw "Server stopped during startup.`n$detail"
        }
        try {
            $response = Invoke-WebRequest -UseBasicParsing -Uri "http://$urlHost`:$Port/api/workspace" -TimeoutSec 1
            if ($response.StatusCode -eq 200) { $ready = $true; break }
        }
        catch { Start-Sleep -Milliseconds 250 }
    }
    if (-not $ready) { throw "Server did not become ready within 10 seconds. See $errorLogPath" }
    Write-Host "`nWorkbench is ready: $url" -ForegroundColor Green
    Write-Host "Press Ctrl+C to stop the service.`n"
    if (-not $NoBrowser) { Start-Process $url }
    Wait-Process -Id $server.Id
}
finally {
    if ($server -and -not $server.HasExited) { Stop-Process -Id $server.Id -Force -ErrorAction SilentlyContinue }
}
