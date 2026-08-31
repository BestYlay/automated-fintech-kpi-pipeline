[CmdletBinding()]
param()

$ErrorActionPreference = "Stop"
$projectRoot = Split-Path -Parent $PSScriptRoot
$configDir = Join-Path $env:LOCALAPPDATA "FinTechPipeline"
$pythonPathFile = Join-Path $configDir "python_path.txt"
$hongKongZone = [TimeZoneInfo]::FindSystemTimeZoneById("China Standard Time")
$hongKongNow = [TimeZoneInfo]::ConvertTimeFromUtc([DateTime]::UtcNow, $hongKongZone)
$taskThrough = $hongKongNow.Date.AddDays(-1).ToString("yyyy-MM-dd")
$taskLog = Join-Path $projectRoot ("logs\local_task_{0}.txt" -f $taskThrough)

function Read-ProtectedValue([string]$path) {
    if (-not (Test-Path -LiteralPath $path)) {
        throw "Protected configuration is missing: $path"
    }
    $cipher = (Get-Content -LiteralPath $path -Raw).Trim()
    $secure = ConvertTo-SecureString $cipher
    $handle = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($secure)
    try {
        return [Runtime.InteropServices.Marshal]::PtrToStringBSTR($handle)
    }
    finally {
        [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($handle)
    }
}

New-Item -ItemType Directory -Force -Path (Join-Path $projectRoot "logs") | Out-Null
Add-Content -LiteralPath $taskLog -Value ("task_started={0} through={1}" -f (Get-Date -Format o), $taskThrough)

$localUrl = Read-ProtectedValue (Join-Path $configDir "pipeline_database_url.dpapi")
$publishUrl = Read-ProtectedValue (Join-Path $configDir "mart_publish_database_url.dpapi")
$env:PIPELINE_DATABASE_URL = $localUrl
$env:MART_PUBLISH_DATABASE_URL = $publishUrl
$env:PYTHONPATH = Join-Path $projectRoot "src"
$env:PYTHONIOENCODING = "utf-8"

try {
    $python = (Get-Content -LiteralPath $pythonPathFile -Raw).Trim()
    if (-not (Test-Path -LiteralPath $python)) {
        throw "Configured Python executable does not exist: $python"
    }
    Push-Location $projectRoot
    try {
        & $python -m fintech_pipeline daily --through $taskThrough --workers 2 --lookback-days 45 --publish 2>&1 |
            Tee-Object -FilePath $taskLog -Append
        $exitCode = $LASTEXITCODE
    }
    finally {
        Pop-Location
    }
    Add-Content -LiteralPath $taskLog -Value ("task_finished={0} exit_code={1}" -f (Get-Date -Format o), $exitCode)
    exit $exitCode
}
catch {
    Add-Content -LiteralPath $taskLog -Value ("task_failed={0} error={1}" -f (Get-Date -Format o), $_.Exception.Message)
    exit 1
}
finally {
    Remove-Item Env:PIPELINE_DATABASE_URL -ErrorAction SilentlyContinue
    Remove-Item Env:MART_PUBLISH_DATABASE_URL -ErrorAction SilentlyContinue
    Remove-Item Env:PYTHONPATH -ErrorAction SilentlyContinue
    Remove-Item Env:PYTHONIOENCODING -ErrorAction SilentlyContinue
}
