[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)][string]$Start,
    [Parameter(Mandatory = $true)][string]$End
)

$ErrorActionPreference = "Stop"
$projectRoot = Split-Path -Parent $PSScriptRoot
$configDir = Join-Path $env:LOCALAPPDATA "FinTechPipeline"
$python = (Get-Content -LiteralPath (Join-Path $configDir "python_path.txt") -Raw).Trim()

function Read-ProtectedValue([string]$path) {
    $cipher = (Get-Content -LiteralPath $path -Raw).Trim()
    $secure = ConvertTo-SecureString $cipher
    $handle = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($secure)
    try { return [Runtime.InteropServices.Marshal]::PtrToStringBSTR($handle) }
    finally { [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($handle) }
}

$env:PIPELINE_DATABASE_URL = Read-ProtectedValue (Join-Path $configDir "pipeline_database_url.dpapi")
$env:MART_PUBLISH_DATABASE_URL = Read-ProtectedValue (Join-Path $configDir "mart_publish_database_url.dpapi")
$env:PYTHONPATH = Join-Path $projectRoot "src"
Push-Location $projectRoot
try {
    & $python -m fintech_pipeline publish-marts --start $Start --end $End
    exit $LASTEXITCODE
}
finally {
    Pop-Location
    Remove-Item Env:PIPELINE_DATABASE_URL -ErrorAction SilentlyContinue
    Remove-Item Env:MART_PUBLISH_DATABASE_URL -ErrorAction SilentlyContinue
    Remove-Item Env:PYTHONPATH -ErrorAction SilentlyContinue
}
