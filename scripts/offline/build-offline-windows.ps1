[CmdletBinding()]
param(
    [Parameter(ValueFromRemainingArguments = $true)]
    [string[]]$Arguments
)

$ErrorActionPreference = "Stop"
$ProjectRoot = Split-Path -Parent (Split-Path -Parent $PSScriptRoot)
$Builder = Join-Path $PSScriptRoot "build_offline_bundle.py"
Set-Location $ProjectRoot

if (Get-Command py.exe -ErrorAction SilentlyContinue) {
    $targetArguments = if ($Arguments -contains "--target") { @() } else { @("--target", "windows") }
    & py.exe -3 $Builder @targetArguments @Arguments
}
elseif (Get-Command python.exe -ErrorAction SilentlyContinue) {
    $targetArguments = if ($Arguments -contains "--target") { @() } else { @("--target", "windows") }
    & python.exe $Builder @targetArguments @Arguments
}
else {
    throw "Python 3.11 or later was not found."
}

if ($LASTEXITCODE -ne 0) {
    throw "Project deployment package build failed with exit code $LASTEXITCODE."
}
