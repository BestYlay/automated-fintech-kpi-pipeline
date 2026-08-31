[CmdletBinding()]
param(
    [switch]$RemoveCredentials
)

$ErrorActionPreference = "Stop"
$taskName = "AutomatedFinTechKPIDaily"
$configDir = Join-Path $env:LOCALAPPDATA "FinTechPipeline"

Unregister-ScheduledTask -TaskName $taskName -Confirm:$false -ErrorAction SilentlyContinue
if ($RemoveCredentials) {
    foreach ($name in @(
        "pipeline_database_url.dpapi",
        "mart_publish_database_url.dpapi",
        "read_only_database_url.dpapi",
        "python_path.txt"
    )) {
        Remove-Item -LiteralPath (Join-Path $configDir $name) -Force -ErrorAction SilentlyContinue
    }
}

Write-Host "Removed scheduled task $taskName."
if ($RemoveCredentials) {
    Write-Host "Removed local DPAPI configuration files."
} else {
    Write-Host "Credentials were retained. Use -RemoveCredentials to remove them."
}
