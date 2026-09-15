#Requires -Version 5.1
# Shared install logic for autoSAXS Windows installer (no WinForms).
Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$script:LastCondaLogLines = @()
$DefaultEnvName = "autosaxs"
# Keep in sync with autosaxs.cli.package_update
$PipSpecStable = "autosaxs[gui]"
$PipSpecNightbuilt = "autosaxs[gui] @ git+https://github.com/MikhailLifar/autoSAXS.git"
$script:InstallLogFile = $null
$script:InstallIconIco = $null

function Resolve-InstallPipSpec {
    param(
        [Parameter(Mandatory = $true)][string]$InstallSource
    )
    $src = $InstallSource.Trim().ToLowerInvariant()
    if ($src -eq "nightbuilt") { return $PipSpecNightbuilt }
    return $PipSpecStable
}

function Get-InstallSourceLabel {
    param(
        [Parameter(Mandatory = $true)][string]$InstallSource
    )
    $src = $InstallSource.Trim().ToLowerInvariant()
    if ($src -eq "nightbuilt") { return "nightbuilt (GitHub)" }
    return "stable (PyPI)"
}

function Initialize-InstallLib {
    param([string]$AssetsDir)
    if ($AssetsDir) {
        $script:InstallIconIco = Join-Path $AssetsDir "autosaxs_icon.ico"
    }
}

function Test-CommandOnPath {
    param([Parameter(Mandatory = $true)][string]$Name)
    $cmd = Get-Command $Name -ErrorAction SilentlyContinue
    return ($null -ne $cmd)
}

function Test-GitPresent {
    return (Test-CommandOnPath -Name "git.exe") -or (Test-CommandOnPath -Name "git")
}

function Test-AtsasPresent {
    # Same signal as autosaxs doctor / probe_atsas: dammif on PATH.
    return (Test-CommandOnPath -Name "dammif.exe") -or (Test-CommandOnPath -Name "dammif")
}

function Write-InstallLogLine {
    param([string]$Line)
    if ([string]::IsNullOrWhiteSpace($Line)) { return }
    if ($script:InstallLogFile) {
        [System.IO.File]::AppendAllText($script:InstallLogFile, $Line + [Environment]::NewLine)
    }
}

function Write-InstallStatus {
    param([string]$Text)
    if ($Text) {
        Write-InstallLogLine -Line ("STATUS:" + $Text)
    }
}

function Test-CondaEnvName {
    param([Parameter(Mandatory = $true)][string]$Name)
    $n = $Name.Trim()
    if ($n.Length -lt 1 -or $n.Length -gt 64) { return $false }
    return ($n -match '^[a-zA-Z0-9][a-zA-Z0-9._-]*$')
}

function Resolve-CondaExeFromRoot {
    param([Parameter(Mandatory = $true)][string]$RootDir)
    $root = $RootDir.Trim().Trim('"')
    if (-not $root -or -not (Test-Path -LiteralPath $root -PathType Container)) {
        return $null
    }
    $candidates = @(
        (Join-Path $root "Scripts\conda.exe"),
        (Join-Path $root "bin\conda"),
        (Join-Path $root "conda.exe")
    )
    foreach ($p in $candidates) {
        if ($p -and (Test-Path -LiteralPath $p)) { return $p }
    }
    return $null
}

function Test-CondaExe {
    param([Parameter(Mandatory = $true)][string]$CondaExe)
    if (-not (Test-Path -LiteralPath $CondaExe)) { return $false }
    try {
        $psi = New-Object System.Diagnostics.ProcessStartInfo
        $psi.FileName = $CondaExe
        $psi.Arguments = "--version"
        $psi.UseShellExecute = $false
        $psi.RedirectStandardOutput = $true
        $psi.RedirectStandardError = $true
        $psi.CreateNoWindow = $true
        $p = [System.Diagnostics.Process]::Start($psi)
        [void]$p.WaitForExit(15000)
        return ($p.HasExited -and $p.ExitCode -eq 0)
    }
    catch {
        return $false
    }
}

function Find-CondaExe {
    $cmd = Get-Command conda.exe -ErrorAction SilentlyContinue
    if ($cmd -and (Test-CondaExe -CondaExe $cmd.Source)) { return $cmd.Source }

    $roots = New-Object System.Collections.Generic.List[string]
    $seen = @{}
    foreach ($pattern in @("miniconda*", "Miniconda*", "anaconda*", "Anaconda*")) {
        $dirs = Get-ChildItem -Path $env:USERPROFILE -Directory -Filter $pattern -ErrorAction SilentlyContinue |
            Sort-Object LastWriteTime -Descending
        foreach ($dir in $dirs) {
            $key = $dir.FullName.ToLowerInvariant()
            if (-not $seen.ContainsKey($key)) {
                $seen[$key] = $true
                [void]$roots.Add($dir.FullName)
            }
        }
    }
    foreach ($root in @(
            (Join-Path $env:LOCALAPPDATA "miniconda3"),
            (Join-Path $env:LOCALAPPDATA "Continuum\miniconda3"),
            "C:\ProgramData\miniconda3",
            "C:\ProgramData\anaconda3"
        )) {
        if ($root) {
            $key = $root.ToLowerInvariant()
            if (-not $seen.ContainsKey($key)) {
                $seen[$key] = $true
                [void]$roots.Add($root)
            }
        }
    }
    foreach ($root in $roots) {
        $exe = Resolve-CondaExeFromRoot -RootDir $root
        if ($exe -and (Test-CondaExe -CondaExe $exe)) { return $exe }
    }
    return $null
}

function Get-CondaRoot {
    param([Parameter(Mandatory = $true)][string]$CondaExe)
    return (Split-Path (Split-Path $CondaExe -Parent) -Parent)
}

function Get-CondaEnvPrefix {
    param(
        [Parameter(Mandatory = $true)][string]$CondaExe,
        [Parameter(Mandatory = $true)][string]$EnvName
    )
    $base = Get-CondaRoot -CondaExe $CondaExe
    $prefix = Join-Path $base "envs\$EnvName"
    if (Test-Path -LiteralPath $prefix) { return $prefix }
    return $null
}

function Get-LiveviewExe {
    param([Parameter(Mandatory = $true)][string]$EnvPrefix)
    $exe = Join-Path $EnvPrefix "Scripts\guisaxs-liveview.exe"
    if (Test-Path -LiteralPath $exe) { return $exe }
    return $null
}

function New-LiveviewShortcut {
    param(
        [Parameter(Mandatory = $true)][string]$TargetExe,
        [string]$IconPath = $script:InstallIconIco
    )
    $desktop = [Environment]::GetFolderPath("Desktop")
    $startMenu = Join-Path ([Environment]::GetFolderPath("StartMenu")) "Programs\Autosaxs"
    New-Item -ItemType Directory -Force -Path $startMenu | Out-Null

    $wsh = New-Object -ComObject WScript.Shell
    foreach ($dir in @($desktop, $startMenu)) {
        $lnkPath = Join-Path $dir "GUISAXS-LiveView.lnk"
        $sc = $wsh.CreateShortcut($lnkPath)
        $sc.TargetPath = $TargetExe
        $sc.WorkingDirectory = $desktop
        $sc.Description = "Live-view app for online SAXS processing"
        if ($IconPath -and (Test-Path -LiteralPath $IconPath)) {
            $sc.IconLocation = "$IconPath,0"
        }
        $sc.Save()
    }
    return (Join-Path $desktop "GUISAXS-LiveView.lnk")
}

function Format-CondaLogLine {
    param([string]$Line)
    if ([string]::IsNullOrWhiteSpace($Line)) { return $null }
    $trim = $Line.Trim()
    if ($trim -match '^(Retrieving notices:|Collecting package metadata \(repodata\.json\):|\s*[-\\|/]\s*)+$') {
        return $null
    }
    if ($trim -match '^Channels:$|^ - defaults$|^ - conda-forge$|^Platform:|^Collecting package metadata') {
        return $Line
    }
    if ($trim -match '^[\s\\|/-]+$') { return $null }
    return $Line
}

function Format-InstallLogLine {
    param([string]$Line)
    if ([string]::IsNullOrWhiteSpace($Line)) { return $null }
    $trim = $Line.Trim()
    $trim = $trim -replace "`b", ""
    if ($trim -match '^[\s\\|/-]+$') { return $null }
    if ($trim -match '^\d+%\|' -or $trim -match '^[.\s]+$') { return $null }
    if ($trim -match 'Collecting package metadata \(repodata\.json\):') {
        return "Collecting package metadata (repodata.json)..."
    }
    if ($trim -match '^(Preparing transaction|Verifying transaction|Executing transaction|Solving environment|Downloading and Extracting Packages):') {
        return ($trim -replace '([\\|/\-\s])+done$', ' done' -replace '([\\|/\-\s])+$', '...')
    }
    if ($trim -match '^(Collecting|Downloading|Installing|Successfully installed|Requirement already satisfied|Building wheel|Built |Using cached|Preparing metadata|Resolving dependencies|Obtaining |Reading |Installing collected packages|Processing |ERROR:|WARNING:)') {
        return $trim
    }
    if ($trim -match '[\\|/\-]{4,}') { return $null }
    return (Format-CondaLogLine -Line $Line)
}

function Get-CondaCreateArgs {
    param([Parameter(Mandatory = $true)][string]$EnvName)
    return @("create", "-n", $EnvName, "python=3.12", "pip", "-y")
}

function Get-CondaFailureMessage {
    param(
        [Parameter(Mandatory = $true)][string]$Step,
        [int]$ExitCode = 1
    )
    $tail = ($script:LastCondaLogLines | Select-Object -Last 24) -join "`r`n"
    if ($tail -match 'NameResolutionError|getaddrinfo failed|Could not resolve|CondaHTTPError|CONNECTION FAILED|ConnectionError|Failed to connect|Network is unreachable') {
        return @(
            "$Step failed (exit $ExitCode): could not reach the package download server (DNS/network)."
            ""
            "Check your internet connection, VPN, proxy, or firewall."
            "Stable installs need pypi.org; nightbuilt installs also need github.com."
            ""
            $tail
        ) -join "`r`n"
    }
    if ($tail -match 'WinError 2|cannot find the file') {
        return @(
            "$Step failed (exit $ExitCode): conda could not read a downloaded package file."
            ""
            "This often means an incomplete download (proxy/antivirus) or a corrupted conda package cache."
            "Try: open Anaconda Prompt, run  conda clean --all -y  , then run this installer again."
            ""
            $tail
        ) -join "`r`n"
    }
    if ($tail) {
        return "$Step failed (exit $ExitCode).`r`n`r`n$tail"
    }
    return "$Step failed (exit $ExitCode)."
}

function Format-CondaArguments {
    param([Parameter(Mandatory = $true)][string[]]$ArgumentList)
    # Windows CreateProcess / ProcessStartInfo quoting (not cmd.exe).
    return (($ArgumentList | ForEach-Object {
            $a = [string]$_
            if ($a -match '[ \t"]') {
                '"' + ($a.Replace('"', '""')) + '"'
            }
            else {
                $a
            }
        }) -join ' ')
}

function Get-EnvPrefixPath {
    param(
        [Parameter(Mandatory = $true)][string]$CondaExe,
        [Parameter(Mandatory = $true)][string]$EnvName
    )
    $prefix = Get-CondaEnvPrefix -CondaExe $CondaExe -EnvName $EnvName
    if ($prefix -and (Test-Path -LiteralPath $prefix)) { return $prefix }
    $fallback = Join-Path (Get-CondaRoot -CondaExe $CondaExe) "envs\$EnvName"
    if (Test-Path -LiteralPath $fallback) { return $fallback }
    throw "Could not find conda environment '$EnvName'."
}

function Get-EnvPythonExe {
    param([Parameter(Mandatory = $true)][string]$EnvPrefix)
    $py = Join-Path $EnvPrefix "python.exe"
    if (-not (Test-Path -LiteralPath $py)) {
        throw "python.exe not found in $EnvPrefix"
    }
    return $py
}

function Add-InstallProcessLine {
    param([string]$Line)
    $formatted = Format-InstallLogLine -Line $Line
    if ($null -eq $formatted) { return }
    [void]$script:StreamProcessLines.Add($formatted)
    Write-InstallLogLine -Line $formatted
}

function Ensure-InstallProcessUtil {
    if ("Autosaxs.Install.ProcessUtil" -as [type]) { return }
    Add-Type -TypeDefinition @"
using System;
using System.Collections.Concurrent;
using System.Diagnostics;
using System.Threading;

namespace Autosaxs.Install {
    public static class ProcessUtil {
        public static readonly ConcurrentQueue<string> Lines = new ConcurrentQueue<string>();

        public static void Reset() {
            string ignored;
            while (Lines.TryDequeue(out ignored)) { }
        }

        public static void OnData(object sender, DataReceivedEventArgs e) {
            if (e.Data != null) {
                Lines.Enqueue(e.Data);
            }
        }

        public static Process Start(ProcessStartInfo psi) {
            Reset();
            psi.UseShellExecute = false;
            psi.RedirectStandardOutput = true;
            psi.RedirectStandardError = true;
            psi.CreateNoWindow = true;
            Process p = new Process();
            p.StartInfo = psi;
            p.OutputDataReceived += OnData;
            p.ErrorDataReceived += OnData;
            p.Start();
            p.BeginOutputReadLine();
            p.BeginErrorReadLine();
            return p;
        }
    }
}
"@
}

function Drain-InstallProcessLines {
    $line = $null
    while ([Autosaxs.Install.ProcessUtil]::Lines.TryDequeue([ref]$line)) {
        Add-InstallProcessLine -Line $line
    }
}

function Invoke-StreamProcess {
    param(
        [Parameter(Mandatory = $true)][System.Diagnostics.ProcessStartInfo]$StartInfo
    )
    $script:StreamProcessLines = New-Object System.Collections.Generic.List[string]
    Ensure-InstallProcessUtil

    # Run the executable directly (no cmd.exe). cmd /c quote parsing breaks
    # pip specs that contain spaces, e.g. nightbuilt "autosaxs[gui] @ git+...".
    $p = [Autosaxs.Install.ProcessUtil]::Start($StartInfo)
    try {
        while (-not $p.HasExited) {
            Drain-InstallProcessLines
            Start-Sleep -Milliseconds 100
            $p.Refresh()
        }
        [void]$p.WaitForExit()
        Start-Sleep -Milliseconds 150
        Drain-InstallProcessLines
        $script:LastCondaLogLines = $script:StreamProcessLines.ToArray()
        return $p.ExitCode
    }
    finally {
        if ($null -ne $p) {
            try { $p.Dispose() } catch { }
        }
    }
}

function Invoke-EnvPython {
    param(
        [Parameter(Mandatory = $true)][string]$PythonExe,
        [Parameter(Mandatory = $true)][string[]]$ArgumentList
    )
    $psi = New-Object System.Diagnostics.ProcessStartInfo
    $psi.FileName = $PythonExe
    $psi.Arguments = Format-CondaArguments -ArgumentList $ArgumentList
    $psi.WorkingDirectory = Split-Path $PythonExe -Parent
    $psi.UseShellExecute = $false
    $psi.RedirectStandardOutput = $true
    $psi.RedirectStandardError = $true
    $psi.CreateNoWindow = $true
    return (Invoke-StreamProcess -StartInfo $psi)
}

function Resolve-LiveviewLauncher {
    param([Parameter(Mandatory = $true)][string]$EnvPrefix)
    $exe = Join-Path $EnvPrefix "Scripts\guisaxs-liveview.exe"
    if (Test-Path -LiteralPath $exe) { return $exe }

    $py = Get-EnvPythonExe -EnvPrefix $EnvPrefix
    $check = & $py -c "import importlib.metadata as m; eps=[e for e in m.entry_points(group='console_scripts') if e.name=='guisaxs-liveview']; print('ok' if eps else '')"
    if (($check | Select-Object -Last 1).Trim() -ne 'ok') { return $null }

    $wrapper = Join-Path $EnvPrefix "Scripts\guisaxs-liveview.cmd"
    $wrapperContent = "@echo off`r`n""$py"" -m guisaxs_liveview %*`r`n"
    [System.IO.File]::WriteAllText($wrapper, $wrapperContent)
    return $wrapper
}

function Invoke-Conda {
    param(
        [Parameter(Mandatory = $true)][string]$CondaExe,
        [Parameter(Mandatory = $true)][string[]]$ArgumentList
    )
    $condaRoot = Get-CondaRoot -CondaExe $CondaExe
    $prevAlwaysYes = $env:CONDA_ALWAYS_YES
    $prevCondaRoot = $env:CONDA_ROOT
    $prevCondaExe = $env:CONDA_EXE
    $env:CONDA_ALWAYS_YES = "true"
    $env:CONDA_ROOT = $condaRoot
    $env:CONDA_EXE = $CondaExe
    $cert = Join-Path $condaRoot "Library\ssl\cacert.pem"
    $prevSsl = $env:SSL_CERT_FILE
    $prevReq = $env:REQUESTS_CA_BUNDLE
    if (Test-Path -LiteralPath $cert) {
        $env:SSL_CERT_FILE = $cert
        $env:REQUESTS_CA_BUNDLE = $cert
    }
    try {
        $psi = New-Object System.Diagnostics.ProcessStartInfo
        $psi.FileName = $CondaExe
        $psi.Arguments = Format-CondaArguments -ArgumentList $ArgumentList
        $psi.WorkingDirectory = $condaRoot
        $psi.UseShellExecute = $false
        $psi.RedirectStandardOutput = $true
        $psi.RedirectStandardError = $true
        $psi.CreateNoWindow = $true
        return (Invoke-StreamProcess -StartInfo $psi)
    }
    finally {
        if ($null -ne $prevAlwaysYes) { $env:CONDA_ALWAYS_YES = $prevAlwaysYes }
        else { Remove-Item Env:CONDA_ALWAYS_YES -ErrorAction SilentlyContinue }
        if ($null -ne $prevCondaRoot) { $env:CONDA_ROOT = $prevCondaRoot }
        else { Remove-Item Env:CONDA_ROOT -ErrorAction SilentlyContinue }
        if ($null -ne $prevCondaExe) { $env:CONDA_EXE = $prevCondaExe }
        else { Remove-Item Env:CONDA_EXE -ErrorAction SilentlyContinue }
        if ($null -ne $prevSsl) { $env:SSL_CERT_FILE = $prevSsl }
        else { Remove-Item Env:SSL_CERT_FILE -ErrorAction SilentlyContinue }
        if ($null -ne $prevReq) { $env:REQUESTS_CA_BUNDLE = $prevReq }
        else { Remove-Item Env:REQUESTS_CA_BUNDLE -ErrorAction SilentlyContinue }
    }
}

function Invoke-CondaCreateEnvironment {
    param(
        [Parameter(Mandatory = $true)][string]$CondaExe,
        [Parameter(Mandatory = $true)][string]$EnvName
    )
    $createArgs = Get-CondaCreateArgs -EnvName $EnvName
    $code = Invoke-Conda -CondaExe $CondaExe -ArgumentList $createArgs
    if ($code -eq 0) { return 0 }

    $logText = ($script:LastCondaLogLines -join "`n")
    if ($logText -match 'WinError 2|cannot find the file') {
        Write-InstallLogLine -Line "Clearing incomplete conda package downloads and retrying once..."
        [void](Invoke-Conda -CondaExe $CondaExe -ArgumentList @("clean", "--packages", "-y"))
        return (Invoke-Conda -CondaExe $CondaExe -ArgumentList $createArgs)
    }
    return $code
}

function Get-EnvPrefixCandidate {
    param(
        [Parameter(Mandatory = $true)][string]$CondaExe,
        [Parameter(Mandatory = $true)][string]$EnvName
    )
    return (Join-Path (Get-CondaRoot -CondaExe $CondaExe) "envs\$EnvName")
}

function Test-EnvHealthy {
    param(
        [Parameter(Mandatory = $true)][string]$CondaExe,
        [Parameter(Mandatory = $true)][string]$EnvName
    )
    $prefix = Get-EnvPrefixCandidate -CondaExe $CondaExe -EnvName $EnvName
    if (-not (Test-Path -LiteralPath $prefix -PathType Container)) { return $false }
    return (Test-Path -LiteralPath (Join-Path $prefix "python.exe"))
}

function Remove-BrokenCondaEnv {
    param(
        [Parameter(Mandatory = $true)][string]$CondaExe,
        [Parameter(Mandatory = $true)][string]$EnvName
    )
    $prefix = Get-EnvPrefixCandidate -CondaExe $CondaExe -EnvName $EnvName
    $listed = Test-EnvExists -CondaExe $CondaExe -EnvName $EnvName

    if ($listed) {
        Write-InstallLogLine -Line "Removing incomplete conda environment '$EnvName'..."
        $code = Invoke-Conda -CondaExe $CondaExe -ArgumentList @("env", "remove", "-n", $EnvName, "-y")
        if ($code -ne 0) {
            Write-InstallLogLine -Line "conda env remove exited with code $code; continuing with folder cleanup..."
        }
    }

    if (Test-Path -LiteralPath $prefix) {
        Write-InstallLogLine -Line "Removing leftover folder: $prefix"
        try {
            Remove-Item -LiteralPath $prefix -Recurse -Force -ErrorAction Stop
        }
        catch {
            throw @(
                "Could not delete broken environment folder:"
                "  $prefix"
                ""
                "Close any terminals or programs using this environment, delete that folder manually,"
                "then run the installer again."
                ""
                $_.Exception.Message
            ) -join "`r`n"
        }
    }
}

function Test-EnvExists {
    param(
        [Parameter(Mandatory = $true)][string]$CondaExe,
        [Parameter(Mandatory = $true)][string]$EnvName
    )
    $psi = New-Object System.Diagnostics.ProcessStartInfo
    $psi.FileName = $CondaExe
    $psi.Arguments = "env list"
    $psi.UseShellExecute = $false
    $psi.RedirectStandardOutput = $true
    $psi.RedirectStandardError = $true
    $psi.CreateNoWindow = $true
    $p = [System.Diagnostics.Process]::Start($psi)
    $out = $p.StandardOutput.ReadToEnd()
    [void]$p.WaitForExit()
    foreach ($line in ($out -split "`r?`n")) {
        if ($line -match ("^\s*" + [regex]::Escape($EnvName) + "(\s|$)")) { return $true }
    }
    return $false
}

function Ensure-GitForNightbuilt {
    param(
        [Parameter(Mandatory = $true)][string]$CondaExe,
        [Parameter(Mandatory = $true)][string]$EnvName,
        [Parameter(Mandatory = $true)][string]$EnvPrefix
    )
    $gitExe = Join-Path $EnvPrefix "Library\bin\git.exe"
    if (Test-Path -LiteralPath $gitExe) { return }
    $gitCmd = Get-Command git.exe -ErrorAction SilentlyContinue
    if ($gitCmd) { return }

    Write-InstallStatus -Text "Installing git into the conda environment (needed for nightbuilt)..."
    Write-InstallLogLine -Line "Nightbuilt install needs git; installing git into '$EnvName'..."
    $code = Invoke-Conda -CondaExe $CondaExe -ArgumentList @("install", "-n", $EnvName, "git", "-y")
    if ($code -ne 0) {
        throw (Get-CondaFailureMessage -Step "conda install git" -ExitCode $code)
    }
    if (-not (Test-Path -LiteralPath $gitExe)) {
        $gitCmd = Get-Command git.exe -ErrorAction SilentlyContinue
        if (-not $gitCmd) {
            throw "git is required for nightbuilt installs but was not found after conda install git."
        }
    }
}

function Invoke-InstallWorkflow {
    param(
        [Parameter(Mandatory = $true)][hashtable]$State
    )
    $condaPath = [string]$State.CondaPath
    $envName = [string]$State.EnvName
    $createShortcut = [bool]$State.CreateShortcut
    $installSource = if ($State.ContainsKey("InstallSource") -and $State.InstallSource) {
        [string]$State.InstallSource
    }
    else {
        "stable"
    }
    $pipSpec = Resolve-InstallPipSpec -InstallSource $installSource
    $sourceLabel = Get-InstallSourceLabel -InstallSource $installSource

    if (-not $condaPath) { throw "conda not found" }
    Write-InstallLogLine -Line "Using conda: $condaPath"
    Write-InstallLogLine -Line "Install source: $sourceLabel"

    $exists = Test-EnvExists -CondaExe $condaPath -EnvName $envName
    $prefix = Get-EnvPrefixCandidate -CondaExe $condaPath -EnvName $envName
    $folderExists = Test-Path -LiteralPath $prefix

    if ($exists -and -not (Test-EnvHealthy -CondaExe $condaPath -EnvName $envName)) {
        Remove-BrokenCondaEnv -CondaExe $condaPath -EnvName $envName
        $exists = $false
        $folderExists = $false
    }
    elseif ($folderExists -and -not $exists) {
        Write-InstallLogLine -Line "Found leftover environment folder from a previous failed install."
        Remove-BrokenCondaEnv -CondaExe $condaPath -EnvName $envName
        $folderExists = $false
    }

    if (-not $exists) {
        Write-InstallStatus -Text "Creating conda environment (python 3.12)..."
        Write-InstallLogLine -Line "Creating environment '$envName' (python 3.12)..."
        $code = Invoke-CondaCreateEnvironment -CondaExe $condaPath -EnvName $envName
        if ($code -ne 0) { throw (Get-CondaFailureMessage -Step "conda create" -ExitCode $code) }
    }
    else {
        Write-InstallStatus -Text "Updating existing environment..."
        Write-InstallLogLine -Line "Environment '$envName' already exists - upgrading package..."
    }

    $prefix = Get-EnvPrefixPath -CondaExe $condaPath -EnvName $envName
    $envPython = Get-EnvPythonExe -EnvPrefix $prefix

    if ($installSource.Trim().ToLowerInvariant() -eq "nightbuilt") {
        Ensure-GitForNightbuilt -CondaExe $condaPath -EnvName $envName -EnvPrefix $prefix
    }

    Write-InstallStatus -Text ("Installing autosaxs ($sourceLabel); may take 5-15 minutes...")
    Write-InstallLogLine -Line "Installing $pipSpec into $prefix ..."
    Write-InstallLogLine -Line "Using $envPython"
    Write-InstallLogLine -Line "pip output will appear below as packages are resolved and downloaded..."
    $code = Invoke-EnvPython -PythonExe $envPython -ArgumentList @(
        "-u", "-m", "pip", "install", "-U",
        $pipSpec
    )
    if ($code -ne 0) { throw (Get-CondaFailureMessage -Step "pip install" -ExitCode $code) }

    Write-InstallStatus -Text "Verifying installation..."
    $pipShow = & $envPython -m pip show autosaxs 2>&1 | Out-String
    foreach ($line in ($pipShow -split "`r?`n")) {
        if ($line -match '^(Name|Version|Location):') {
            Write-InstallLogLine -Line $line.Trim()
        }
    }
    if ($pipShow -notmatch [regex]::Escape($prefix)) {
        throw "autosaxs was not installed into $prefix. pip targeted a different Python environment."
    }

    $liveviewExe = Resolve-LiveviewLauncher -EnvPrefix $prefix
    if (-not $liveviewExe) {
        throw "guisaxs-liveview launcher not found under $prefix\Scripts - GUI extra may have failed."
    }
    Write-InstallLogLine -Line "LiveView: $liveviewExe"

    if ($createShortcut) {
        Write-InstallStatus -Text "Creating Desktop shortcuts..."
        Write-InstallLogLine -Line "Creating Desktop / Start Menu shortcuts..."
        $shortcutPath = New-LiveviewShortcut -TargetExe $liveviewExe
        Write-InstallLogLine -Line "Shortcut: $shortcutPath"
    }

    return @{
        Ok          = $true
        LiveviewExe = $liveviewExe
    }
}
