# Installing autoSAXS

This guide is for **Linux** and **Windows**.

**Beginners:** you only need Miniconda (once) and a small **installer ZIP**. You do **not** need to clone the repository, and you do not need to type conda or Python commands.

**ATSAS** (proprietary SAXS software from EMBL) is **not** included. You do **not** need it for calibration, integration, buffer subtraction, or the live GUI. See [Optional: ATSAS](#optional-atsas) if you later need p(r), DAMMIF, and similar tools.

---

## For everyone (recommended)

### 1. Install Miniconda (once)

Miniconda is a free program that provides Python for scientific software.

1. Open: [https://docs.anaconda.com/miniconda/miniconda-install/](https://docs.anaconda.com/miniconda/miniconda-install/)
2. Download the installer for **Windows** or **Linux**.
3. Run it and accept the defaults.
   - **Windows:** use **Anaconda Prompt** afterward if the installer did not add conda to PATH.
4. Finish the installer, then continue below.

### 2. Download the autoSAXS installer

Get the ZIP for your system from the project’s GitHub Releases page:

[https://github.com/MikhailLifar/autoSAXS/releases](https://github.com/MikhailLifar/autoSAXS/releases)

| System  | File |
|---------|------|
| Windows | `autoSAXS-installer-windows.zip` |
| Linux   | `autoSAXS-installer-linux.zip` |

Unzip it anywhere convenient (for example your Desktop).

*(Developers building a ZIP from a source checkout: run `bash scripts/pack_installers.sh` — outputs under `dist/`.)*

### 3. Run the installer

**Windows:** double-click **`Install autoSAXS.bat`** (the only file at the top level of the ZIP).

**Linux:** double-click `Install-autoSAXS.sh` (or right-click → Run as Program). If the file manager asks, allow executing as a program. You need `zenity` or `kdialog` (most desktops already have one; on Ubuntu: `sudo apt install zenity`).

Follow the windows:

1. **Prerequisites** — the installer looks for Miniconda. If it is missing, open the Miniconda download page, install it, then click **Retry**.
2. **Options** — leave **Create Desktop shortcut** checked (recommended).
3. **Installing** — wait while packages download (needs internet; may take several minutes).
4. **Finish** — you can open GUISAXS-LiveView from the installer, or close it.

### 4. Start autoSAXS every day

Double-click **GUISAXS-LiveView** on your Desktop (or from the Start Menu / application menu).

Open **Help** in the top toolbar and follow the guidance.

---

## Optional: ATSAS

You do **not** need ATSAS to calibrate, integrate, subtract buffer, or use GUISAXS-LiveView for live processing.

Install ATSAS when you want tools that call EMBL programs (for example pair-distance distribution p(r), DAMMIF, BODIES, MIXTURE, or `process-monodisperse`).

Download and run the official installer, and ensure ATSAS programs (such as `dammif`) are on your PATH:

[https://www.embl-hamburg.de/biosaxs/download.html](https://www.embl-hamburg.de/biosaxs/download.html)

Recommended version for autoSAXS: **3.2.1**. After installing, you can check with `autosaxs doctor` (see Developers).

| Works without ATSAS | Needs ATSAS |
|---------------------|-------------|
| calibrate, integrate, average, integrate_proxy, subtract | fit_distances (p(r)) |
| plot, plot_2d, fit_guinier, analyze_kratky | fit_sizes (D(R) via GNOM) |
| model_dr_mc (McSAS), model_density (DENSS) | model_dam, model_bodies, model_mixture |
| report_individual, report_summary | process_monodisperse |

---

## Troubleshooting

**Installer says Miniconda / conda was not found**

- Install Miniconda from the link in step 1, then click **Retry** in the installer.
- On Windows, try running the installer again from **Anaconda Prompt** after Miniconda is installed.

**Linux: “needs zenity or kdialog”**

- Install a dialog tool, e.g. `sudo apt install zenity`, then run `Install-autoSAXS.sh` again.

**No Desktop shortcut**

- Re-run the installer and leave **Create Desktop shortcut** checked.
- Or start LiveView from Anaconda Prompt / terminal after `conda activate autosaxs` with `guisaxs-liveview` (developers).

**GUI does not open (Linux)**

- You need a graphical desktop session.

**Install is slow or fails**

- Check your internet connection (packages come from conda-forge / PyPI).
- Run the installer again; an existing `autosaxs` environment will be upgraded in place.

---

## Developers

Clone the repository for development. Optional conda file [`environment.yml`](environment.yml) is for editable / scripted setups — **beginners do not need it**.

```bash
conda create -n autosaxs python=3.12 pip -y
conda activate autosaxs
python -m pip install -e ".[gui]"
autosaxs doctor
guisaxs-liveview
```

CLI-only (no GUI extras):

```bash
python -m pip install autosaxs
autosaxs doctor
```

Build beginner installer ZIPs:

```bash
bash scripts/pack_installers.sh
# → dist/autoSAXS-installer-windows.zip
# → dist/autoSAXS-installer-linux.zip
```

Health check: `autosaxs doctor`.
