#Requires -Version 5.1
<#
.SYNOPSIS
  Lightweight WinForms installer for autoSAXS (no global Python required).

.DESCRIPTION
  Pages: (1) find conda  (2) options — Desktop shortcut  (3) install progress  (4) finish.
  Installs via: conda create -n autosaxs + pip install "autosaxs[gui]" from PyPI.
#>
Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

Add-Type -AssemblyName System.Windows.Forms
Add-Type -AssemblyName System.Drawing

$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$AssetsDir = Join-Path $ScriptDir "assets"
$IconIco = Join-Path $AssetsDir "autosaxs_icon.ico"
$EnvName = "autosaxs"
$MinicondaUrl = "https://docs.anaconda.com/miniconda/miniconda-install/"
$PipSpec = "autosaxs[gui]"

function Find-CondaExe {
    $cmd = Get-Command conda.exe -ErrorAction SilentlyContinue
    if ($cmd) { return $cmd.Source }

    $candidates = @(
        (Join-Path $env:USERPROFILE "miniconda3\Scripts\conda.exe"),
        (Join-Path $env:USERPROFILE "Miniconda3\Scripts\conda.exe"),
        (Join-Path $env:USERPROFILE "anaconda3\Scripts\conda.exe"),
        (Join-Path $env:USERPROFILE "Anaconda3\Scripts\conda.exe"),
        (Join-Path $env:LOCALAPPDATA "miniconda3\Scripts\conda.exe"),
        (Join-Path $env:LOCALAPPDATA "Continuum\miniconda3\Scripts\conda.exe"),
        "C:\ProgramData\miniconda3\Scripts\conda.exe",
        "C:\ProgramData\anaconda3\Scripts\conda.exe"
    )
    foreach ($p in $candidates) {
        if ($p -and (Test-Path -LiteralPath $p)) { return $p }
    }
    return $null
}

function Get-CondaEnvPrefix {
    param([Parameter(Mandatory = $true)][string]$CondaExe)
    $base = Split-Path (Split-Path $CondaExe -Parent) -Parent
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
        [string]$IconPath = $IconIco
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

function Append-Log {
    param($Box, [string]$Line)
    if (-not $Line) { return }
    $Box.AppendText($Line + "`r`n")
    $Box.SelectionStart = $Box.Text.Length
    $Box.ScrollToCaret()
    [System.Windows.Forms.Application]::DoEvents()
}

function Invoke-Conda {
    param(
        [Parameter(Mandatory = $true)][string]$CondaExe,
        [Parameter(Mandatory = $true)][string[]]$ArgumentList,
        [System.Windows.Forms.TextBox]$LogBox = $null
    )
    $outFile = [System.IO.Path]::GetTempFileName()
    $errFile = [System.IO.Path]::GetTempFileName()
    try {
        $p = Start-Process -FilePath $CondaExe -ArgumentList $ArgumentList `
            -NoNewWindow -Wait -PassThru `
            -RedirectStandardOutput $outFile -RedirectStandardError $errFile
        if ($LogBox) {
            foreach ($line in (Get-Content -LiteralPath $outFile -ErrorAction SilentlyContinue)) {
                Append-Log $LogBox $line
            }
            foreach ($line in (Get-Content -LiteralPath $errFile -ErrorAction SilentlyContinue)) {
                Append-Log $LogBox $line
            }
        }
        return $p.ExitCode
    }
    finally {
        Remove-Item -LiteralPath $outFile, $errFile -Force -ErrorAction SilentlyContinue
    }
}

function Test-EnvExists {
    param([Parameter(Mandatory = $true)][string]$CondaExe)
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

# --- UI ---
$form = New-Object System.Windows.Forms.Form
$form.Text = "Install autoSAXS"
$form.Size = New-Object System.Drawing.Size(560, 420)
$form.StartPosition = "CenterScreen"
$form.FormBorderStyle = "FixedDialog"
$form.MaximizeBox = $false
$form.MinimizeBox = $false
if (Test-Path -LiteralPath $IconIco) {
    try { $form.Icon = New-Object System.Drawing.Icon($IconIco) } catch { }
}

$page = 1
$script:CondaPath = Find-CondaExe
$script:CreateShortcut = $true
$script:InstallOk = $false
$script:LiveviewExe = $null
$script:Page = 1

# Shared controls
$lblTitle = New-Object System.Windows.Forms.Label
$lblTitle.Font = New-Object System.Drawing.Font("Segoe UI", 12, [System.Drawing.FontStyle]::Bold)
$lblTitle.Location = New-Object System.Drawing.Point(16, 12)
$lblTitle.Size = New-Object System.Drawing.Size(510, 28)
$form.Controls.Add($lblTitle)

$panel = New-Object System.Windows.Forms.Panel
$panel.Location = New-Object System.Drawing.Point(16, 48)
$panel.Size = New-Object System.Drawing.Size(510, 270)
$form.Controls.Add($panel)

$btnBack = New-Object System.Windows.Forms.Button
$btnBack.Text = "Back"
$btnBack.Location = New-Object System.Drawing.Point(250, 340)
$btnBack.Size = New-Object System.Drawing.Size(90, 28)
$form.Controls.Add($btnBack)

$btnNext = New-Object System.Windows.Forms.Button
$btnNext.Text = "Next"
$btnNext.Location = New-Object System.Drawing.Point(350, 340)
$btnNext.Size = New-Object System.Drawing.Size(90, 28)
$form.Controls.Add($btnNext)

$btnCancel = New-Object System.Windows.Forms.Button
$btnCancel.Text = "Cancel"
$btnCancel.Location = New-Object System.Drawing.Point(450, 340)
$btnCancel.Size = New-Object System.Drawing.Size(80, 28)
$btnCancel.Add_Click({ $form.Close() })
$form.Controls.Add($btnCancel)

function Clear-Panel {
    $panel.Controls.Clear()
}

function Show-Page1 {
    Clear-Panel
    $script:Page = 1
    $lblTitle.Text = "1 / 4  —  Prerequisites"
    $btnBack.Enabled = $false
    $btnNext.Text = "Next"
    $btnCancel.Enabled = $true

    $script:CondaPath = Find-CondaExe

    $lbl = New-Object System.Windows.Forms.Label
    $lbl.Location = New-Object System.Drawing.Point(0, 0)
    $lbl.Size = New-Object System.Drawing.Size(510, 120)
    $lbl.Font = New-Object System.Drawing.Font("Segoe UI", 9)
    if ($script:CondaPath) {
        $lbl.Text = "Miniconda / Anaconda found.`r`n`r`nconda:`r`n$($script:CondaPath)`r`n`r`nClick Next to continue."
        $btnNext.Enabled = $true
    }
    else {
        $lbl.Text = "autoSAXS needs Miniconda (a free Python toolbox).`r`n`r`nIt was not found on this computer.`r`n`r`n1. Install Miniconda with the official installer.`r`n2. Close and reopen any terminals if asked.`r`n3. Click Retry here."
        $btnNext.Enabled = $false
    }
    $panel.Controls.Add($lbl)

    $btnDocs = New-Object System.Windows.Forms.Button
    $btnDocs.Text = "Open Miniconda download page"
    $btnDocs.Location = New-Object System.Drawing.Point(0, 140)
    $btnDocs.Size = New-Object System.Drawing.Size(240, 30)
    $btnDocs.Add_Click({ Start-Process $MinicondaUrl })
    $panel.Controls.Add($btnDocs)

    $btnRetry = New-Object System.Windows.Forms.Button
    $btnRetry.Text = "Retry"
    $btnRetry.Location = New-Object System.Drawing.Point(250, 140)
    $btnRetry.Size = New-Object System.Drawing.Size(100, 30)
    $btnRetry.Add_Click({ Show-Page1 })
    $panel.Controls.Add($btnRetry)
}

function Show-Page2 {
    Clear-Panel
    $script:Page = 2
    $lblTitle.Text = "2 / 4  —  Options"
    $btnBack.Enabled = $true
    $btnNext.Text = "Install"
    $btnNext.Enabled = $true
    $btnCancel.Enabled = $true

    $lbl = New-Object System.Windows.Forms.Label
    $lbl.Location = New-Object System.Drawing.Point(0, 0)
    $lbl.Size = New-Object System.Drawing.Size(510, 60)
    $lbl.Text = "Choose install options, then click Install.`r`nThis will create a conda environment named '$EnvName' and install autoSAXS with the desktop GUI."
    $panel.Controls.Add($lbl)

    $chk = New-Object System.Windows.Forms.CheckBox
    $chk.Text = "Create Desktop shortcut (GUISAXS-LiveView)"
    $chk.Location = New-Object System.Drawing.Point(0, 80)
    $chk.Size = New-Object System.Drawing.Size(400, 24)
    $chk.Checked = $script:CreateShortcut
    $chk.Add_CheckedChanged({ $script:CreateShortcut = $chk.Checked })
    $panel.Controls.Add($chk)
}

function Show-Page3-And-Install {
    Clear-Panel
    $script:Page = 3
    $lblTitle.Text = "3 / 4  —  Installing"
    $btnBack.Enabled = $false
    $btnNext.Enabled = $false
    $btnCancel.Enabled = $false
    $btnNext.Text = "Next"

    $log = New-Object System.Windows.Forms.TextBox
    $log.Multiline = $true
    $log.ScrollBars = "Vertical"
    $log.ReadOnly = $true
    $log.Location = New-Object System.Drawing.Point(0, 0)
    $log.Size = New-Object System.Drawing.Size(510, 260)
    $log.Font = New-Object System.Drawing.Font("Consolas", 8)
    $panel.Controls.Add($log)

    try {
        if (-not $script:CondaPath) { throw "conda not found" }
        Append-Log $log "Using conda: $($script:CondaPath)"

        $exists = Test-EnvExists -CondaExe $script:CondaPath
        if (-not $exists) {
            Append-Log $log "Creating environment '$EnvName' (python 3.12)…"
            $code = Invoke-Conda -CondaExe $script:CondaPath -ArgumentList @("create", "-n", $EnvName, "python=3.12", "pip", "-y") -LogBox $log
            if ($code -ne 0) { throw "conda create failed (exit $code)" }
        }
        else {
            Append-Log $log "Environment '$EnvName' already exists — upgrading package…"
        }

        Append-Log $log "Installing $PipSpec …"
        $code = Invoke-Conda -CondaExe $script:CondaPath -ArgumentList @(
            "run", "-n", $EnvName, "python", "-m", "pip", "install", "-U", $PipSpec
        ) -LogBox $log
        if ($code -ne 0) { throw "pip install failed (exit $code)" }

        $prefix = Get-CondaEnvPrefix -CondaExe $script:CondaPath
        if (-not $prefix) {
            $tmp = & $script:CondaPath run -n $EnvName python -c "import sys; print(sys.prefix)"
            $prefix = ($tmp | Select-Object -Last 1).Trim()
        }
        $script:LiveviewExe = Get-LiveviewExe -EnvPrefix $prefix
        if (-not $script:LiveviewExe) {
            throw "guisaxs-liveview.exe not found under $prefix\Scripts — GUI extra may have failed."
        }
        Append-Log $log "LiveView: $($script:LiveviewExe)"

        if ($script:CreateShortcut) {
            Append-Log $log "Creating Desktop / Start Menu shortcuts…"
            $lnk = New-LiveviewShortcut -TargetExe $script:LiveviewExe
            Append-Log $log "Shortcut: $lnk"
        }

        $script:InstallOk = $true
        Append-Log $log "Done."
        $btnNext.Enabled = $true
        $btnNext.Text = "Next"
    }
    catch {
        Append-Log $log ("ERROR: " + $_.Exception.Message)
        $script:InstallOk = $false
        $btnCancel.Enabled = $true
        $btnCancel.Text = "Close"
        [System.Windows.Forms.MessageBox]::Show(
            "Installation failed.`r`n`r`n$($_.Exception.Message)`r`n`r`nSee the log in the installer window.",
            "Install autoSAXS",
            "OK",
            "Error"
        ) | Out-Null
    }
}

function Show-Page4 {
    Clear-Panel
    $script:Page = 4
    $lblTitle.Text = "4 / 4  —  Finished"
    $btnBack.Enabled = $false
    $btnNext.Text = "Close"
    $btnNext.Enabled = $true
    $btnCancel.Visible = $false

    $lbl = New-Object System.Windows.Forms.Label
    $lbl.Location = New-Object System.Drawing.Point(0, 0)
    $lbl.Size = New-Object System.Drawing.Size(510, 120)
    if ($script:InstallOk) {
        $extra = if ($script:CreateShortcut) {
            "`r`n`r`nA Desktop shortcut GUISAXS-LiveView was created. Double-click it to start."
        }
        else { "" }
        $lbl.Text = "autoSAXS was installed successfully.$extra"
    }
    else {
        $lbl.Text = "Installation did not complete successfully. You can close this window and run the installer again."
    }
    $panel.Controls.Add($lbl)

    if ($script:InstallOk -and $script:LiveviewExe) {
        $btnOpen = New-Object System.Windows.Forms.Button
        $btnOpen.Text = "Open GUISAXS-LiveView"
        $btnOpen.Location = New-Object System.Drawing.Point(0, 140)
        $btnOpen.Size = New-Object System.Drawing.Size(200, 32)
        $btnOpen.Add_Click({
                Start-Process -FilePath $script:LiveviewExe -WorkingDirectory ([Environment]::GetFolderPath("Desktop"))
            })
        $panel.Controls.Add($btnOpen)
    }
}

$btnBack.Add_Click({
        if ($script:Page -eq 2) { Show-Page1 }
    })

$btnNext.Add_Click({
        if ($script:Page -eq 1) {
            if (-not (Find-CondaExe)) {
                [System.Windows.Forms.MessageBox]::Show(
                    "conda is still not found. Install Miniconda, then click Retry.",
                    "Install autoSAXS",
                    "OK",
                    "Warning"
                ) | Out-Null
                return
            }
            $script:CondaPath = Find-CondaExe
            Show-Page2
        }
        elseif ($script:Page -eq 2) {
            Show-Page3-And-Install
        }
        elseif ($script:Page -eq 3) {
            Show-Page4
        }
        elseif ($script:Page -eq 4) {
            $form.Close()
        }
    })

Show-Page1
[void]$form.ShowDialog()
if ($script:InstallOk) { exit 0 } else { exit 1 }
