#Requires -Version 5.1
param(
    [Parameter(Mandatory = $true)][string]$CondaPath,
    [Parameter(Mandatory = $true)][string]$EnvName,
    [Parameter(Mandatory = $true)][string]$CreateShortcut,
    [Parameter(Mandatory = $true)][string]$LogFile,
    [Parameter(Mandatory = $true)][string]$ResultFile,
    [Parameter(Mandatory = $true)][string]$AssetsDir
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$LibPath = Join-Path $PSScriptRoot "Install-autoSAXS-lib.ps1"
. $LibPath

$script:InstallLogFile = $LogFile
Initialize-InstallLib -AssetsDir $AssetsDir

trap {
    if ($script:InstallLogFile) {
        Write-InstallLogLine -Line ("ERROR: " + $_.Exception.Message)
        if ($_.ScriptStackTrace) {
            Write-InstallLogLine -Line $_.ScriptStackTrace
        }
    }
    exit 1
}

try {
    $state = @{
        CondaPath      = $CondaPath
        EnvName        = $EnvName
        CreateShortcut = ($CreateShortcut -eq 'true')
    }
    $result = Invoke-InstallWorkflow -State $state
    $payload = @{
        Ok          = $true
        LiveviewExe = [string]$result.LiveviewExe
    }
    $json = $payload | ConvertTo-Json -Compress
    $utf8NoBom = New-Object System.Text.UTF8Encoding $false
    [System.IO.File]::WriteAllText($ResultFile, $json, $utf8NoBom)
    exit 0
}
catch {
    $msg = $_.Exception.Message
    if ($msg) {
        Write-InstallLogLine -Line ("ERROR: " + $msg)
    }
    exit 1
}
