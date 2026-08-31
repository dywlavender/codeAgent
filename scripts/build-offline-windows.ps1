[CmdletBinding()]
param(
    [Parameter(ValueFromRemainingArguments = $true)]
    [string[]]$Arguments
)

$ErrorActionPreference = "Stop"
$ProjectRoot = Split-Path -Parent $PSScriptRoot
$Builder = Join-Path $PSScriptRoot "build_offline_bundle.py"
Set-Location $ProjectRoot

if (Get-Command py.exe -ErrorAction SilentlyContinue) {
    & py.exe -3 $Builder @Arguments
}
elseif (Get-Command python.exe -ErrorAction SilentlyContinue) {
    & python.exe $Builder @Arguments
}
else {
    throw "Python 3.11 or later was not found."
}

if ($LASTEXITCODE -ne 0) {
    throw "Offline bundle build failed with exit code $LASTEXITCODE."
}
