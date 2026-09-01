#Requires -Version 5.1
<#
.SYNOPSIS
  Lightweight WinForms installer for autoSAXS (no global Python required).

.DESCRIPTION
  Pages: (1) find conda  (2) options - Desktop shortcut  (3) install progress  (4) finish.
  Installs via: conda create -n autosaxs + pip install "autosaxs[gui]" from PyPI.
#>
Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

Add-Type -AssemblyName System.Windows.Forms
Add-Type -AssemblyName System.Drawing

$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$AssetsDir = Join-Path $ScriptDir "assets"
if (-not (Test-Path -LiteralPath $AssetsDir)) {
    $AssetsDir = Join-Path (Split-Path $ScriptDir -Parent) "assets"
}
$IconIco = Join-Path $AssetsDir "autosaxs_icon.ico"
$DefaultEnvName = "autosaxs"
$MinicondaUrl = "https://docs.anaconda.com/miniconda/miniconda-install/"
. (Join-Path $ScriptDir "Install-autoSAXS-lib.ps1")
Initialize-InstallLib -AssetsDir $AssetsDir

function Append-InstallLog {
    param([string]$Line)
    if (-not $Line -or -not $script:InstallLogBox) { return }
    $script:InstallLogBox.AppendText($Line + "`r`n")
    $script:InstallLogBox.SelectionStart = $script:InstallLogBox.Text.Length
    $script:InstallLogBox.ScrollToCaret()
}

function Update-InstallLogView {
    if (-not $script:InstallLogFile -or -not (Test-Path -LiteralPath $script:InstallLogFile)) { return }
    $size = [int](Get-Item -LiteralPath $script:InstallLogFile).Length
    if ($size -le $script:InstallLogOffset) { return }
    $stream = [System.IO.File]::Open(
        $script:InstallLogFile,
        [System.IO.FileMode]::Open,
        [System.IO.FileAccess]::Read,
        [System.IO.FileShare]::ReadWrite
    )
    try {
        [void]$stream.Seek($script:InstallLogOffset, [System.IO.SeekOrigin]::Begin)
        $reader = New-Object System.IO.StreamReader($stream)
        $chunk = $reader.ReadToEnd()
        $reader.Dispose()
    }
    finally {
        $stream.Dispose()
    }
    $script:InstallLogOffset = $size
    if (-not $chunk) { return }
    foreach ($line in ($chunk -split "`r?`n")) {
        if (-not $line) { continue }
        if ($line.StartsWith("STATUS:")) {
            if ($null -ne $script:InstallStatus) {
                $script:InstallStatus.Text = $line.Substring(7)
            }
        }
        else {
            Append-InstallLog -Line $line
        }
    }
}

function Stop-InstallWorkers {
    $script:InstallRunning = $false
    if ($null -ne $script:InstallPollTimer) {
        $script:InstallPollTimer.Stop()
        $script:InstallPollTimer.Dispose()
        $script:InstallPollTimer = $null
    }
    if ($null -ne $script:InstallHeartbeat) {
        $script:InstallHeartbeat.Stop()
        $script:InstallHeartbeat.Dispose()
        $script:InstallHeartbeat = $null
    }
    if ($null -ne $script:InstallProgress) {
        $script:InstallProgress.Style = "Continuous"
        $script:InstallProgress.Value = 100
    }
}

function Test-InstallWorkerFinished {
    if ($null -eq $script:InstallWorkerProcess) { return $false }
    $script:InstallWorkerProcess.Refresh()
    return $script:InstallWorkerProcess.HasExited
}

function Read-InstallResultFile {
    if (-not (Test-Path -LiteralPath $script:InstallResultFile)) { return $null }
    $json = [System.IO.File]::ReadAllText($script:InstallResultFile)
    if ($json.Length -gt 0 -and [int][char]$json[0] -eq 0xFEFF) {
        $json = $json.Substring(1)
    }
    if (-not $json.Trim()) { return $null }
    return ($json | ConvertFrom-Json)
}

function Complete-InstallSuccess {
    param([string]$LiveviewExe)
    if ($script:InstallCompletionHandled) { return }
    $script:InstallCompletionHandled = $true
    Stop-InstallWorkers
    $script:InstallOk = $true
    $script:LiveviewExe = $LiveviewExe
    if ($null -ne $script:InstallStatus) {
        $script:InstallStatus.Text = "Installation complete."
        $script:InstallStatus.ForeColor = [System.Drawing.Color]::FromArgb(0, 100, 0)
    }
    Append-InstallLog -Line "Done."
    $btnNext.Enabled = $true
    $btnCancel.Enabled = $false
}

function Complete-InstallFailure {
    param([string]$Message)
    if ($script:InstallCompletionHandled) { return }
    $script:InstallCompletionHandled = $true
    Stop-InstallWorkers
    $script:InstallOk = $false
    if ($null -ne $script:InstallStatus) {
        $script:InstallStatus.Text = "Installation failed."
        $script:InstallStatus.ForeColor = [System.Drawing.Color]::DarkRed
    }
    if ($Message) {
        $alreadyLogged = $false
        if ($script:InstallLogBox -and $script:InstallLogBox.Text -match [regex]::Escape($Message)) {
            $alreadyLogged = $true
        }
        if (-not $alreadyLogged) {
            Append-InstallLog -Line ("ERROR: " + $Message)
        }
    }
    $btnCancel.Enabled = $true
    $btnCancel.Text = "Close"
    [System.Windows.Forms.MessageBox]::Show(
        "Installation failed.`r`n`r`n$Message`r`n`r`nSee the log in the installer window.",
        "Install autoSAXS",
        "OK",
        "Error"
    ) | Out-Null
}

function Show-InstallPollTick {
    if ($script:InstallCompletionHandled) { return }
    Update-InstallLogView
    if (-not (Test-InstallWorkerFinished)) { return }

    Update-InstallLogView
    $script:InstallWorkerProcess.Refresh()
    $exitCode = $script:InstallWorkerProcess.ExitCode

    if ($exitCode -eq 0) {
        try {
            $result = Read-InstallResultFile
            if ($null -ne $result -and $result.Ok) {
                Complete-InstallSuccess -LiveviewExe ([string]$result.LiveviewExe)
                return
            }
        }
        catch {
            Complete-InstallFailure -Message $_.Exception.Message
            return
        }
    }

    $err = "Installation worker exited with code $exitCode."
    if (Test-Path -LiteralPath $script:InstallLogFile) {
        $tail = Get-Content -LiteralPath $script:InstallLogFile -Tail 40 -ErrorAction SilentlyContinue
        if ($tail) {
            $errorLine = $null
            $failedLine = $null
            foreach ($line in $tail) {
                if ($line.StartsWith("ERROR:")) { $errorLine = $line.Substring(6).Trim() }
                if ($line -match 'failed \(exit') { $failedLine = $line.Trim() }
            }
            if ($errorLine) {
                $err = $errorLine
            }
            elseif ($failedLine) {
                $err = $failedLine
            }
            elseif ($exitCode -eq 2) {
                $err = @(
                    "The installer worker crashed during conda create (exit code 2)."
                    "This usually means a broken leftover environment folder or a conda download error."
                    "Delete the environment folder under envs\$($script:EnvName) if it still exists, then retry."
                ) -join " "
            }
            foreach ($line in $tail) {
                if ($line -and -not $line.StartsWith("STATUS:")) {
                    Append-InstallLog -Line $line
                }
            }
        }
    }
    Complete-InstallFailure -Message $err
}

# --- UI ---
$UiFont = New-Object System.Drawing.Font("Segoe UI", 9)
$UiFontBold = New-Object System.Drawing.Font("Segoe UI", 10, [System.Drawing.FontStyle]::Bold)
$UiFontTitle = New-Object System.Drawing.Font("Segoe UI", 11, [System.Drawing.FontStyle]::Bold)
$UiFontMono = New-Object System.Drawing.Font("Consolas", 9)
$UiMargin = 24
$UiContentWidth = 592

$form = New-Object System.Windows.Forms.Form
$form.Text = "Install autoSAXS"
$form.ClientSize = New-Object System.Drawing.Size(640, 520)
$form.StartPosition = "CenterScreen"
$form.FormBorderStyle = "FixedDialog"
$form.MaximizeBox = $false
$form.MinimizeBox = $false
$form.BackColor = [System.Drawing.Color]::FromArgb(245, 245, 245)
$form.Font = $UiFont
if (Test-Path -LiteralPath $IconIco) {
    try { $form.Icon = New-Object System.Drawing.Icon($IconIco) } catch { }
}

$script:CondaPath = Find-CondaExe
$script:EnvName = $DefaultEnvName
$script:CreateShortcut = $true
$script:InstallOk = $false
$script:LiveviewExe = $null
$script:Page = 1
$script:UiRefs = @{}
$script:InstallHeartbeat = $null
$script:InstallRunning = $false
$script:InstallLogBox = $null
$script:InstallStatus = $null
$script:InstallProgress = $null
$script:InstallWorkerProcess = $null
$script:InstallPollTimer = $null
$script:InstallLogFile = $null
$script:InstallResultFile = $null
$script:InstallLogOffset = 0
$script:InstallCompletionHandled = $false

$lblTitle = New-Object System.Windows.Forms.Label
$lblTitle.Font = $UiFontTitle
$lblTitle.Location = New-Object System.Drawing.Point($UiMargin, 16)
$lblTitle.Size = New-Object System.Drawing.Size($UiContentWidth, 28)
$lblTitle.ForeColor = [System.Drawing.Color]::FromArgb(32, 32, 32)
$form.Controls.Add($lblTitle)

$lblSubtitle = New-Object System.Windows.Forms.Label
$lblSubtitle.Font = $UiFont
$lblSubtitle.Location = New-Object System.Drawing.Point($UiMargin, 44)
$lblSubtitle.Size = New-Object System.Drawing.Size($UiContentWidth, 36)
$lblSubtitle.ForeColor = [System.Drawing.Color]::FromArgb(80, 80, 80)
$form.Controls.Add($lblSubtitle)

$panel = New-Object System.Windows.Forms.Panel
$panel.Location = New-Object System.Drawing.Point($UiMargin, 84)
$panel.Size = New-Object System.Drawing.Size($UiContentWidth, 360)
$panel.BackColor = [System.Drawing.Color]::White
$panel.BorderStyle = "FixedSingle"
$panel.AutoScroll = $true
$form.Controls.Add($panel)

$footer = New-Object System.Windows.Forms.Panel
$footer.Location = New-Object System.Drawing.Point(0, 456)
$footer.Size = New-Object System.Drawing.Size(640, 56)
$footer.BackColor = [System.Drawing.Color]::FromArgb(245, 245, 245)
$form.Controls.Add($footer)

$btnBack = New-Object System.Windows.Forms.Button
$btnBack.Text = "< Back"
$btnBack.Size = New-Object System.Drawing.Size(96, 32)
$btnBack.Location = New-Object System.Drawing.Point(328, 12)
$btnBack.FlatStyle = "System"
$footer.Controls.Add($btnBack)

$btnNext = New-Object System.Windows.Forms.Button
$btnNext.Text = "Next >"
$btnNext.Size = New-Object System.Drawing.Size(96, 32)
$btnNext.Location = New-Object System.Drawing.Point(432, 12)
$btnNext.FlatStyle = "System"
$footer.Controls.Add($btnNext)

$btnCancel = New-Object System.Windows.Forms.Button
$btnCancel.Text = "Cancel"
$btnCancel.Size = New-Object System.Drawing.Size(96, 32)
$btnCancel.Location = New-Object System.Drawing.Point(528, 12)
$btnCancel.FlatStyle = "System"
$btnCancel.Add_Click({ $form.Close() })
$footer.Controls.Add($btnCancel)

function Clear-Panel {
    $panel.Controls.Clear()
    $script:UiRefs = @{}
}

function New-UiGroupBox {
    param(
        [string]$Text,
        [int]$X,
        [int]$Y,
        [int]$Width,
        [int]$Height
    )
    $gb = New-Object System.Windows.Forms.GroupBox
    $gb.Text = $Text
    $gb.Location = New-Object System.Drawing.Point($X, $Y)
    $gb.Size = New-Object System.Drawing.Size($Width, $Height)
    $gb.Font = $UiFontBold
    $gb.ForeColor = [System.Drawing.Color]::FromArgb(48, 48, 48)
    return $gb
}

function New-UiLabel {
    param(
        [string]$Text,
        [int]$X,
        [int]$Y,
        [int]$Width,
        [int]$Height = 0,
        [switch]$Bold
    )
    $lbl = New-Object System.Windows.Forms.Label
    $lbl.Text = $Text
    $lbl.Location = New-Object System.Drawing.Point($X, $Y)
    if ($Height -gt 0) {
        $lbl.Size = New-Object System.Drawing.Size($Width, $Height)
    }
    else {
        $lbl.Size = New-Object System.Drawing.Size($Width, 40)
        $lbl.AutoSize = $false
    }
    $lbl.Font = if ($Bold) { $UiFontBold } else { $UiFont }
    $lbl.ForeColor = [System.Drawing.Color]::FromArgb(64, 64, 64)
    return $lbl
}

function Set-Page1CondaReady {
    param(
        [Parameter(Mandatory = $true)][string]$CondaExe
    )
    if (-not (Test-CondaExe -CondaExe $CondaExe)) {
        [System.Windows.Forms.MessageBox]::Show(
            "That folder does not look like a Miniconda / Anaconda install.`r`n`r`nPick the top-level folder that contains Scripts\conda.exe (for example C:\Users\You\miniconda3).",
            "Install autoSAXS",
            "OK",
            "Warning"
        ) | Out-Null
        return $false
    }
    $script:CondaPath = $CondaExe
    $status = $script:UiRefs.StatusLabel
    $pathBox = $script:UiRefs.CondaPathBox
    if ($status) {
        $status.Text = "Ready to continue.`r`n`r`nconda:`r`n$CondaExe"
        $status.ForeColor = [System.Drawing.Color]::FromArgb(0, 100, 0)
    }
    if ($pathBox) {
        $pathBox.Text = (Split-Path (Split-Path $CondaExe -Parent) -Parent)
    }
    $btnNext.Enabled = $true
    return $true
}

function Show-Page1 {
    Clear-Panel
    $script:Page = 1
    $lblTitle.Text = "Install autoSAXS"
    $lblSubtitle.Text = "Step 1 of 4 - Find Miniconda or Anaconda"
    $btnBack.Enabled = $false
    $btnNext.Text = "Next >"
    $btnCancel.Enabled = $true
    $btnCancel.Visible = $true

    $autoConda = Find-CondaExe
    if ($autoConda) {
        $script:CondaPath = $autoConda
    }

    $pad = 16
    $innerW = $UiContentWidth - (2 * $pad) - 4

    $gbStatus = New-UiGroupBox -Text "Status" -X $pad -Y $pad -Width $innerW -Height 108
    $panel.Controls.Add($gbStatus)

    $statusText = if ($script:CondaPath) {
        "Miniconda / Anaconda was found.`r`n`r`nconda:`r`n$($script:CondaPath)"
    }
    else {
        "Miniconda was not found automatically.`r`nInstall it from the link below, then click Retry search."
    }
    $lblStatus = New-UiLabel -Text $statusText -X 12 -Y 24 -Width ($innerW - 24) -Height 72
    if ($script:CondaPath) {
        $lblStatus.ForeColor = [System.Drawing.Color]::FromArgb(0, 100, 0)
    }
    $gbStatus.Controls.Add($lblStatus)
    $script:UiRefs.StatusLabel = $lblStatus
    $btnNext.Enabled = [bool]$script:CondaPath

    $gbFolder = New-UiGroupBox -Text "Conda directory path" -X $pad -Y ($pad + 120) -Width $innerW -Height 112
    $panel.Controls.Add($gbFolder)

    $lblFolder = New-UiLabel -Text "Top-level Miniconda / Anaconda folder:" -X 12 -Y 24 -Width ($innerW - 24) -Height 18
    $gbFolder.Controls.Add($lblFolder)

    $txtRoot = New-Object System.Windows.Forms.TextBox
    $txtRoot.Location = New-Object System.Drawing.Point(12, 46)
    $txtRoot.Size = New-Object System.Drawing.Size(($innerW - 124), 24)
    if ($script:CondaPath) {
        $txtRoot.Text = (Split-Path (Split-Path $script:CondaPath -Parent) -Parent)
    }
    $gbFolder.Controls.Add($txtRoot)
    $script:UiRefs.CondaPathBox = $txtRoot

    $btnBrowse = New-Object System.Windows.Forms.Button
    $btnBrowse.Text = "Browse..."
    $btnBrowse.Location = New-Object System.Drawing.Point(($innerW - 104), 44)
    $btnBrowse.Size = New-Object System.Drawing.Size(92, 28)
    $btnBrowse.Add_Click({
            $pathBox = $script:UiRefs.CondaPathBox
            if (-not $pathBox) { return }
            $dlg = New-Object System.Windows.Forms.FolderBrowserDialog
            $dlg.Description = "Select the top-level Miniconda / Anaconda folder (contains Scripts\conda.exe)"
            if ($pathBox.Text -and (Test-Path -LiteralPath $pathBox.Text)) {
                $dlg.SelectedPath = $pathBox.Text
            }
            if ($dlg.ShowDialog() -eq [System.Windows.Forms.DialogResult]::OK) {
                $pathBox.Text = $dlg.SelectedPath
            }
        })
    $gbFolder.Controls.Add($btnBrowse)

    $btnUseFolder = New-Object System.Windows.Forms.Button
    $btnUseFolder.Text = "Use this folder"
    $btnUseFolder.Location = New-Object System.Drawing.Point(12, 76)
    $btnUseFolder.Size = New-Object System.Drawing.Size(120, 28)
    $btnUseFolder.Add_Click({
            $pathBox = $script:UiRefs.CondaPathBox
            if (-not $pathBox) { return }
            $exe = Resolve-CondaExeFromRoot -RootDir $pathBox.Text
            if (-not $exe) {
                [System.Windows.Forms.MessageBox]::Show(
                    "Could not find conda.exe in that folder.`r`n`r`nSelect the top-level install folder (for example C:\Users\You\miniconda3).",
                    "Install autoSAXS",
                    "OK",
                    "Warning"
                ) | Out-Null
                return
            }
            [void](Set-Page1CondaReady -CondaExe $exe)
        })
    $gbFolder.Controls.Add($btnUseFolder)

    $gbHelp = New-UiGroupBox -Text "Need Miniconda?" -X $pad -Y ($pad + 244) -Width $innerW -Height 72
    $panel.Controls.Add($gbHelp)

    $btnDocs = New-Object System.Windows.Forms.Button
    $btnDocs.Text = "Open download page"
    $btnDocs.Location = New-Object System.Drawing.Point(12, 28)
    $btnDocs.Size = New-Object System.Drawing.Size(150, 28)
    $btnDocs.Add_Click({ Start-Process $MinicondaUrl })
    $gbHelp.Controls.Add($btnDocs)

    $btnRetry = New-Object System.Windows.Forms.Button
    $btnRetry.Text = "Retry search"
    $btnRetry.Location = New-Object System.Drawing.Point(172, 28)
    $btnRetry.Size = New-Object System.Drawing.Size(110, 28)
    $btnRetry.Add_Click({ Show-Page1 })
    $gbHelp.Controls.Add($btnRetry)
}

function Show-Page2 {
    Clear-Panel
    $script:Page = 2
    $lblTitle.Text = "Install autoSAXS"
    $lblSubtitle.Text = "Step 2 of 4 - Choose install options"
    $btnBack.Enabled = $true
    $btnNext.Text = "Install"
    $btnNext.Enabled = $true
    $btnCancel.Enabled = $true

    $pad = 16
    $innerW = $UiContentWidth - (2 * $pad) - 4

    $gbOptions = New-UiGroupBox -Text "Install options" -X $pad -Y $pad -Width $innerW -Height 168
    $panel.Controls.Add($gbOptions)

    $lblIntro = New-UiLabel -Text "autoSAXS will be installed into a dedicated conda environment. You can change the name below." -X 12 -Y 24 -Width ($innerW - 24) -Height 36
    $gbOptions.Controls.Add($lblIntro)

    $lblEnv = New-UiLabel -Text "Environment name:" -X 12 -Y 68 -Width 140 -Height 20
    $gbOptions.Controls.Add($lblEnv)

    $txtEnv = New-Object System.Windows.Forms.TextBox
    $txtEnv.Location = New-Object System.Drawing.Point(152, 66)
    $txtEnv.Size = New-Object System.Drawing.Size(220, 24)
    $txtEnv.Text = $script:EnvName
    $gbOptions.Controls.Add($txtEnv)
    $script:UiRefs.EnvNameBox = $txtEnv

    $lblEnvHint = New-UiLabel -Text "Default: autosaxs" -X 152 -Y 94 -Width 260 -Height 18
    $lblEnvHint.ForeColor = [System.Drawing.Color]::Gray
    $gbOptions.Controls.Add($lblEnvHint)

    $chk = New-Object System.Windows.Forms.CheckBox
    $chk.Text = "Create Desktop shortcut for GUISAXS-LiveView"
    $chk.Location = New-Object System.Drawing.Point(12, 124)
    $chk.Size = New-Object System.Drawing.Size(($innerW - 24), 24)
    $chk.Checked = $script:CreateShortcut
    $gbOptions.Controls.Add($chk)
    $script:UiRefs.ShortcutCheck = $chk
}

function Show-Page3-And-Install {
    Clear-Panel
    $script:Page = 3
    $lblTitle.Text = "Install autoSAXS"
    $lblSubtitle.Text = "Step 3 of 4 - Installing (this may take several minutes)"
    $btnBack.Enabled = $false
    $btnNext.Enabled = $false
    $btnCancel.Enabled = $false
    $btnNext.Text = "Next >"

    $pad = 12
    $innerW = $UiContentWidth - (2 * $pad) - 4

    $script:InstallStatus = New-UiLabel -Text "Starting installation..." -X $pad -Y $pad -Width $innerW -Height 20
    $script:InstallStatus.ForeColor = [System.Drawing.Color]::FromArgb(0, 90, 160)
    $panel.Controls.Add($script:InstallStatus)

    $script:InstallProgress = New-Object System.Windows.Forms.ProgressBar
    $script:InstallProgress.Location = New-Object System.Drawing.Point($pad, ($pad + 24))
    $script:InstallProgress.Size = New-Object System.Drawing.Size($innerW, 18)
    $script:InstallProgress.Style = "Marquee"
    $script:InstallProgress.MarqueeAnimationSpeed = 30
    $panel.Controls.Add($script:InstallProgress)

    $script:InstallLogBox = New-Object System.Windows.Forms.TextBox
    $script:InstallLogBox.Multiline = $true
    $script:InstallLogBox.ScrollBars = "Vertical"
    $script:InstallLogBox.ReadOnly = $true
    $script:InstallLogBox.Location = New-Object System.Drawing.Point($pad, ($pad + 52))
    $script:InstallLogBox.Size = New-Object System.Drawing.Size($innerW, 268)
    $script:InstallLogBox.Font = $UiFontMono
    $script:InstallLogBox.BackColor = [System.Drawing.Color]::FromArgb(252, 252, 252)
    $script:InstallLogBox.BorderStyle = "FixedSingle"
    $panel.Controls.Add($script:InstallLogBox)

    Stop-InstallWorkers
    $script:InstallCompletionHandled = $false

    $script:InstallLogFile = Join-Path $env:TEMP ("autosaxs-install-" + [guid]::NewGuid().ToString() + ".log")
    $script:InstallResultFile = Join-Path $env:TEMP ("autosaxs-install-result-" + [guid]::NewGuid().ToString() + ".json")
    $script:InstallLogOffset = 0
    [System.IO.File]::WriteAllText($script:InstallLogFile, "")

    $workerPs1 = Join-Path $ScriptDir "Install-autoSAXS-worker.ps1"
    if (-not (Test-Path -LiteralPath $workerPs1)) {
        Complete-InstallFailure -Message "Missing installer worker script: $workerPs1"
        return
    }

    $shortcutArg = if ($script:CreateShortcut) { "true" } else { "false" }
    $workerArgs = @(
        "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", $workerPs1,
        "-CondaPath", $script:CondaPath,
        "-EnvName", $script:EnvName,
        "-CreateShortcut", $shortcutArg,
        "-LogFile", $script:InstallLogFile,
        "-ResultFile", $script:InstallResultFile,
        "-AssetsDir", $AssetsDir
    )

    try {
        $script:InstallWorkerProcess = Start-Process -FilePath "powershell.exe" `
            -ArgumentList $workerArgs `
            -PassThru -WindowStyle Hidden -WorkingDirectory $ScriptDir
    }
    catch {
        Complete-InstallFailure -Message $_.Exception.Message
        return
    }

    $script:InstallRunning = $true

    $script:InstallHeartbeat = New-Object System.Windows.Forms.Timer
    $script:InstallHeartbeat.Interval = 30000
    $script:InstallHeartbeat.Add_Tick({
            if ($script:InstallCompletionHandled) { return }
            if (-not $script:InstallRunning -or -not $script:InstallLogBox) { return }
            if (Test-InstallWorkerFinished) {
                Show-InstallPollTick
                return
            }
            Append-InstallLog -Line "... still working (large downloads can take several minutes) ..."
        })
    $script:InstallHeartbeat.Start()

    $script:InstallPollTimer = New-Object System.Windows.Forms.Timer
    $script:InstallPollTimer.Interval = 250
    $script:InstallPollTimer.Add_Tick({ Show-InstallPollTick })
    $script:InstallPollTimer.Start()
}

function Show-Page4 {
    Clear-Panel
    $script:Page = 4
    $lblTitle.Text = "Install autoSAXS"
    $lblSubtitle.Text = "Step 4 of 4 - Finished"
    $btnBack.Enabled = $false
    $btnNext.Text = "Close"
    $btnNext.Enabled = $true
    $btnCancel.Visible = $false

    $pad = 16
    $innerW = $UiContentWidth - (2 * $pad) - 4

    $gbDone = New-UiGroupBox -Text "Result" -X $pad -Y $pad -Width $innerW -Height 180
    $panel.Controls.Add($gbDone)

    $doneText = if ($script:InstallOk) {
        $extra = if ($script:CreateShortcut) {
            "`r`n`r`nA Desktop shortcut for GUISAXS-LiveView was created."
        }
        else { "" }
        "autoSAXS was installed successfully.$extra"
    }
    else {
        "Installation did not complete. Close this window and run the installer again."
    }
    $lbl = New-UiLabel -Text $doneText -X 12 -Y 24 -Width ($innerW - 24) -Height 100
    if ($script:InstallOk) {
        $lbl.ForeColor = [System.Drawing.Color]::FromArgb(0, 100, 0)
    }
    $gbDone.Controls.Add($lbl)

    if ($script:InstallOk -and $script:LiveviewExe) {
        $btnOpen = New-Object System.Windows.Forms.Button
        $btnOpen.Text = "Open GUISAXS-LiveView"
        $btnOpen.Location = New-Object System.Drawing.Point(12, 132)
        $btnOpen.Size = New-Object System.Drawing.Size(200, 32)
        $btnOpen.Add_Click({
                Start-Process -FilePath $script:LiveviewExe -WorkingDirectory ([Environment]::GetFolderPath("Desktop"))
            })
        $gbDone.Controls.Add($btnOpen)
    }
}

function Read-Page2Options {
    $envBox = $script:UiRefs.EnvNameBox
    $chk = $script:UiRefs.ShortcutCheck
    if ($envBox) {
        $script:EnvName = [string]$envBox.Text
        if ($script:EnvName) { $script:EnvName = $script:EnvName.Trim() }
    }
    if ($chk) {
        $script:CreateShortcut = $chk.Checked
    }
}

$btnBack.Add_Click({
        if ($script:Page -eq 2) { Show-Page1 }
    })

$btnNext.Add_Click({
        if ($script:Page -eq 1) {
            if (-not $script:CondaPath -or -not (Test-CondaExe -CondaExe $script:CondaPath)) {
                [System.Windows.Forms.MessageBox]::Show(
                    "conda is still not selected. Install Miniconda, click Retry search, or choose the install folder and click Use this folder.",
                    "Install autoSAXS",
                    "OK",
                    "Warning"
                ) | Out-Null
                return
            }
            Show-Page2
        }
        elseif ($script:Page -eq 2) {
            Read-Page2Options
            if (-not (Test-CondaEnvName -Name $script:EnvName)) {
                [System.Windows.Forms.MessageBox]::Show(
                    "Enter a valid conda environment name (letters, numbers, dots, hyphens, underscores; for example autosaxs).",
                    "Install autoSAXS",
                    "OK",
                    "Warning"
                ) | Out-Null
                return
            }
            Show-Page3-And-Install
        }
        elseif ($script:Page -eq 3) {
            Show-Page4
        }
        elseif ($script:Page -eq 4) {
            $form.Close()
        }
    })

try {
    Show-Page1
    [void]$form.ShowDialog()
    if ($script:InstallOk) { exit 0 } else { exit 1 }
}
catch {
    $msg = $_.Exception.Message
    [System.Windows.Forms.MessageBox]::Show(
        "The installer crashed before it could finish.`r`n`r`n$msg",
        "Install autoSAXS",
        "OK",
        "Error"
    ) | Out-Null
    exit 1
}
