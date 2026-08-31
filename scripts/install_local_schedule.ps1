[CmdletBinding()]
param()

$ErrorActionPreference = "Stop"
$taskName = "AutomatedFinTechKPIDaily"
$projectRoot = Split-Path -Parent $PSScriptRoot
$configDir = Join-Path $env:LOCALAPPDATA "FinTechPipeline"
$runner = Join-Path $PSScriptRoot "run_daily_local.ps1"
$powershell = (Get-Command powershell.exe).Source

function Read-RequiredSecret([string]$label) {
    $secure = Read-Host -Prompt $label -AsSecureString
    $handle = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($secure)
    try {
        $value = [Runtime.InteropServices.Marshal]::PtrToStringBSTR($handle)
    }
    finally {
        [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($handle)
    }
    if ([string]::IsNullOrWhiteSpace($value)) {
        throw "$label cannot be empty"
    }
    return $value
}

function Save-ProtectedValue([string]$name, [string]$value) {
    $secure = ConvertTo-SecureString $value -AsPlainText -Force
    $cipher = ConvertFrom-SecureString $secure
    Set-Content -LiteralPath (Join-Path $configDir $name) -Value $cipher -Encoding ascii -NoNewline
}

New-Item -ItemType Directory -Force -Path $configDir | Out-Null
$localUrl = Read-RequiredSecret "本机 PIPELINE_DATABASE_URL（完整 PostgreSQL URL）"
$publishUrl = Read-RequiredSecret "Neon MART_PUBLISH_DATABASE_URL（direct URL）"
if ($publishUrl -match "-pooler") {
    throw "MART_PUBLISH_DATABASE_URL must be a direct Neon URL, not a -pooler URL"
}
Save-ProtectedValue "pipeline_database_url.dpapi" $localUrl
Save-ProtectedValue "mart_publish_database_url.dpapi" $publishUrl

$python = (Get-Command python.exe -ErrorAction SilentlyContinue).Source
if (-not $python) {
    throw "python.exe was not found on PATH"
}
Set-Content -LiteralPath (Join-Path $configDir "python_path.txt") -Value $python -Encoding utf8 -NoNewline

$action = New-ScheduledTaskAction `
    -Execute $powershell `
    -Argument ("-NoLogo -NoProfile -NonInteractive -ExecutionPolicy Bypass -File `"{0}`"" -f $runner) `
    -WorkingDirectory $projectRoot
$trigger = New-ScheduledTaskTrigger -Daily -At "01:30"
$principal = New-ScheduledTaskPrincipal `
    -UserId ("{0}\{1}" -f $env:USERDOMAIN, $env:USERNAME) `
    -LogonType Interactive `
    -RunLevel Limited
$settings = New-ScheduledTaskSettingsSet `
    -StartWhenAvailable `
    -MultipleInstances IgnoreNew `
    -ExecutionTimeLimit (New-TimeSpan -Hours 12) `
    -RestartCount 3 `
    -RestartInterval (New-TimeSpan -Minutes 30)

Register-ScheduledTask `
    -TaskName $taskName `
    -Action $action `
    -Trigger $trigger `
    -Principal $principal `
    -Settings $settings `
    -Description "Local Hong Kong daily FinTech KPI generation and Neon mart publication" `
    -Force | Out-Null

Write-Host "Installed $taskName at 01:30 local time."
Write-Host "Credentials are DPAPI-protected under $configDir."
Write-Host "The task runs only while the current Windows user is logged in."
