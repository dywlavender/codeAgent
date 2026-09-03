[CmdletBinding()]
param(
    [ValidateSet("Empty", "Repository")]
    [string]$Mode = "Empty",

    [string]$Database = ".data\knowledge.db",
    [string]$Repository,
    [string]$RepositoryId = "repo-main",
    [string]$ProjectConfig,
    [string]$HostAddress = "127.0.0.1",
    [ValidateRange(1, 65535)]
    [int]$Port = 8082,

    [switch]$SkipInstall,
    [switch]$SkipFrontendBuild,
    [switch]$NoBrowser
)

$ErrorActionPreference = "Stop"
$ProgressPreference = "SilentlyContinue"
$ProjectRoot = Split-Path -Parent $PSScriptRoot
$DefaultProjectConfig = Join-Path $ProjectRoot "project.config.json"
Set-Location $ProjectRoot

if ([string]::IsNullOrWhiteSpace($ProjectConfig) -and
    -not $PSBoundParameters.ContainsKey("Mode") -and
    -not $PSBoundParameters.ContainsKey("Repository") -and
    (Test-Path -PathType Leaf $DefaultProjectConfig)) {
    $ProjectConfig = $DefaultProjectConfig
}

if (-not [string]::IsNullOrWhiteSpace($ProjectConfig)) {
    $ProjectConfigPath = [System.IO.Path]::GetFullPath($ProjectConfig)
    if (-not (Test-Path -PathType Leaf $ProjectConfigPath)) {
        throw "Project config does not exist: $ProjectConfigPath"
    }
    try {
        $projectPayload = Get-Content -Raw -Path $ProjectConfigPath | ConvertFrom-Json
    }
    catch {
        throw "Project config cannot be parsed: $ProjectConfigPath"
    }
    $startup = $projectPayload.startup
    if ($null -ne $startup -and $startup -is [string]) {
        throw "Project config startup must be an object: $ProjectConfigPath"
    }
    if ($null -ne $startup -and -not $PSBoundParameters.ContainsKey("Database") -and $null -ne $startup.database) {
        if ($startup.database -isnot [string] -or [string]::IsNullOrWhiteSpace($startup.database)) {
            throw "Project config startup.database must be a non-empty string: $ProjectConfigPath"
        }
        $Database = [string]$startup.database
    }
    if ($null -ne $startup -and -not $PSBoundParameters.ContainsKey("Port") -and $null -ne $startup.port) {
        try {
            $startupPort = [int]$startup.port
        }
        catch {
            throw "Project config startup.port must be an integer: $ProjectConfigPath"
        }
        if ($startupPort -lt 1 -or $startupPort -gt 65535) {
            throw "Project config startup.port must be between 1 and 65535: $ProjectConfigPath"
        }
        $Port = $startupPort
    }
}

function Write-Step([string]$Message) {
    Write-Host "`n==> $Message" -ForegroundColor Cyan
}

function Resolve-PythonLauncher {
    if (Get-Command py.exe -ErrorAction SilentlyContinue) {
        return @{ Command = "py.exe"; Prefix = @("-3") }
    }
    if (Get-Command python.exe -ErrorAction SilentlyContinue) {
        return @{ Command = "python.exe"; Prefix = @() }
    }
    throw "Python 3.11 or later was not found. Install it from https://www.python.org/downloads/windows/ and enable 'Add Python to PATH'."
}

function Invoke-Checked {
    param([string]$Command, [string[]]$Arguments)
    & $Command @Arguments
    if ($LASTEXITCODE -ne 0) {
        throw "Command failed ($LASTEXITCODE): $Command $($Arguments -join ' ')"
    }
}

function Test-PortOpen([string]$Address, [int]$TargetPort) {
    $probeAddress = if ($Address -eq "0.0.0.0" -or $Address -eq "::") { "127.0.0.1" } else { $Address }
    $client = [System.Net.Sockets.TcpClient]::new()
    try {
        $async = $client.BeginConnect($probeAddress, $TargetPort, $null, $null)
        if ($async.AsyncWaitHandle.WaitOne(300) -and $client.Connected) {
            return $true
        }
        return $false
    }
    catch { return $false }
    finally {
        $client.Dispose()
    }
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
        Write-Step "Stopping process listening on port $TargetPort (PID $ownerId)"
        Stop-Process -Id ([int]$ownerId) -Force -ErrorAction SilentlyContinue
    }
    for ($attempt = 0; $attempt -lt 20; $attempt++) {
        if (-not (Test-PortOpen $Address $TargetPort)) { return }
        Start-Sleep -Milliseconds 250
    }
    throw "Port $TargetPort is still in use after stopping its processes."
}

Write-Host "Business Code Agent - Windows launcher" -ForegroundColor Green
Write-Host "Mode: $Mode | Database: $Database"

$launcher = Resolve-PythonLauncher
$versionCode = "import sys; assert sys.version_info >= (3,11), 'Python 3.11+ required'; print(sys.version.split()[0])"
Write-Step "Checking Python"
$pythonCheckArguments = @($launcher.Prefix) + @("-c", $versionCode)
& $launcher.Command @pythonCheckArguments
if ($LASTEXITCODE -ne 0) { throw "Python 3.11 or later is required." }

$VenvRoot = Join-Path $ProjectRoot ".venv-windows"
$VenvPython = Join-Path $VenvRoot "Scripts\python.exe"
if (-not (Test-Path $VenvPython)) {
    Write-Step "Creating Python virtual environment"
    Invoke-Checked $launcher.Command (@($launcher.Prefix) + @("-m", "venv", $VenvRoot))
}

if (-not $SkipInstall) {
    Write-Step "Installing backend"
    try {
        Invoke-Checked $VenvPython @("-m", "pip", "install", "--disable-pip-version-check", "-e", ".[tree-sitter]")
    }
    catch {
        Write-Warning "The optional Tree-sitter adapter could not be installed; continuing without it."
        Invoke-Checked $VenvPython @("-m", "pip", "install", "--disable-pip-version-check", "-e", ".")
    }
}
elseif (-not (Test-Path (Join-Path $VenvRoot "Scripts\business-code-agent.exe"))) {
    throw "-SkipInstall was used, but the backend is not installed in .venv-windows. Run once without -SkipInstall."
}

if (-not $SkipFrontendBuild) {
    Write-Step "Building the Agent workbench"
    if (-not (Get-Command node.exe -ErrorAction SilentlyContinue)) {
        throw "Node.js 20 or later was not found. Install the LTS release from https://nodejs.org/."
    }
    if (-not (Get-Command npm.cmd -ErrorAction SilentlyContinue)) {
        throw "npm was not found. Reinstall the Node.js LTS release."
    }
    & node.exe -e "const major=Number(process.versions.node.split('.')[0]); if(major<20){console.error('Node.js 20+ required'); process.exit(1)}"
    if ($LASTEXITCODE -ne 0) { throw "Node.js 20 or later is required." }
    Push-Location (Join-Path $ProjectRoot "frontend")
    try {
        Invoke-Checked "npm.cmd" @("ci", "--no-audit", "--no-fund")
        Invoke-Checked "npm.cmd" @("run", "build")
    }
    finally {
        Pop-Location
    }
}
elseif (-not (Test-Path (Join-Path $ProjectRoot "frontend\dist\index.html"))) {
    throw "-SkipFrontendBuild was used, but frontend\dist does not exist. Run once without -SkipFrontendBuild."
}

$DatabasePath = if ([System.IO.Path]::IsPathRooted($Database)) { $Database } else { Join-Path $ProjectRoot $Database }
$DatabasePath = [System.IO.Path]::GetFullPath($DatabasePath)
$DataDirectory = Split-Path -Parent $DatabasePath
New-Item -ItemType Directory -Force -Path $DataDirectory | Out-Null

Write-Step "Preparing knowledge database"
if (-not [string]::IsNullOrWhiteSpace($ProjectConfig)) {
    Invoke-Checked $VenvPython @("-m", "business_code_agent.cli", "init-db", "--db", $DatabasePath)
    Invoke-Checked $VenvPython @("-m", "business_code_agent.cli", "sync-project", "--config", $ProjectConfigPath, "--db", $DatabasePath)
}
else {
    switch ($Mode) {
        "Empty" {
            Invoke-Checked $VenvPython @("-m", "business_code_agent.cli", "init-db", "--db", $DatabasePath)
        }
        "Repository" {
            if ([string]::IsNullOrWhiteSpace($Repository)) {
                throw "Repository mode requires -Repository 'C:\path\to\java-project'."
            }
            $RepositoryPath = [System.IO.Path]::GetFullPath($Repository)
            if (-not (Test-Path -PathType Container $RepositoryPath)) {
                throw "Repository directory does not exist: $RepositoryPath"
            }
            Invoke-Checked $VenvPython @("-m", "business_code_agent.cli", "init-db", "--db", $DatabasePath)
            Invoke-Checked $VenvPython @("-m", "business_code_agent.cli", "ingest-repo", $RepositoryPath, "--repository-id", $RepositoryId, "--db", $DatabasePath)
        }
    }
}

if (Test-PortOpen $HostAddress $Port) {
    Write-Step "Port $Port is already in use; stopping the existing process and restarting"
    Stop-PortProcesses $HostAddress $Port
}
if (Test-PortOpen $HostAddress $Port) {
    throw "Port $Port is still in use."
}
$UrlHost = if ($HostAddress -eq "0.0.0.0" -or $HostAddress -eq "::") { "127.0.0.1" } else { $HostAddress }
$Url = "http://$UrlHost`:$Port/"

$LogPath = Join-Path $DataDirectory "server.log"
$ErrorLogPath = Join-Path $DataDirectory "server-error.log"
Remove-Item $LogPath, $ErrorLogPath -Force -ErrorAction SilentlyContinue

Write-Step "Starting workbench"
$quotedDatabasePath = '"' + $DatabasePath.Replace('"', '\"') + '"'
$serverArguments = @("-m", "business_code_agent.cli", "serve-query", "--db", $quotedDatabasePath, "--host", $HostAddress, "--port", "$Port")
if (-not [string]::IsNullOrWhiteSpace($ProjectConfig)) {
    $quotedProjectConfig = '"' + ([System.IO.Path]::GetFullPath($ProjectConfig)).Replace('"', '\"') + '"'
    $serverArguments += @("--project-config", $quotedProjectConfig)
}
$server = Start-Process -FilePath $VenvPython -ArgumentList $serverArguments -PassThru -WindowStyle Hidden -RedirectStandardOutput $LogPath -RedirectStandardError $ErrorLogPath

try {
    $ready = $false
    for ($attempt = 0; $attempt -lt 40; $attempt++) {
        if ($server.HasExited) {
            $detail = if (Test-Path $ErrorLogPath) { Get-Content $ErrorLogPath -Raw } else { "No server error log was written." }
            throw "Server stopped during startup.`n$detail"
        }
        try {
            $response = Invoke-WebRequest -UseBasicParsing -Uri "http://$UrlHost`:$Port/api/workspace" -TimeoutSec 1
            if ($response.StatusCode -eq 200) { $ready = $true; break }
        }
        catch { Start-Sleep -Milliseconds 250 }
    }
    if (-not $ready) { throw "Server did not become ready within 10 seconds. See $ErrorLogPath" }

    Write-Host "`nWorkbench is ready: $Url" -ForegroundColor Green
    Write-Host "Database: $DatabasePath"
    Write-Host "Logs: $LogPath"
    Write-Host "Press Ctrl+C in this window to stop the service.`n"
    if (-not $NoBrowser) { Start-Process $Url }
    Wait-Process -Id $server.Id
}
finally {
    if ($server -and -not $server.HasExited) {
        Stop-Process -Id $server.Id -Force -ErrorAction SilentlyContinue
    }
}
