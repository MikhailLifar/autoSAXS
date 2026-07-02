# GUISAXS Liveview — usage (step list)

**What it does:** watches **one** folder. Each **new** `.tif` / `.tiff` there (or dropped on the middle 2D canvas) joins a **queue** and is processed in order. After **calibration** (and optional **buffer** subtraction), you can turn on **automatic analysis** in the right-hand **Analysis** panel using its **drop-down list**; see step 8 for the modes.

---

## Installation and update to the latest version

You can install the liveview app using pip:

```sh
pip install "autosaxs[gui] @ git+http://hpc.nano.sfedu.ru:8080/mikhail/saxsprocessing.git@main"
```

You may want to do this inside a virtual environment (venv or conda).

---

## Procedure

0. Make sure a Python environment, where `autosaxs[gui]` is installed, is activated in your terminal.
1. Type **`guisaxs-liveview`** from the folder you want to watch. The GUI starts using that directory as the watch folder (or the last folder you used, if it is still valid).
2. To watch a different folder, use **Change watch folder…** at the top of the window.
3. You can feed TIFFs in two ways:
   - **Drag and drop** `.tif` / `.tiff` onto the **middle** panel (“Latest image (2D) — drop .tif here”) (YOU CAN D-N-D MULTIPLE `.tif` / `.tiff` AT ONCE!), **or**
   - **Copy or save** files **directly into** that watch directory (not into subfolders).
5. Watch the **middle** column for queue status and the latest **2D** image.
6. **If calibration is required** → left column → **Set calibration** → follow the wizard (IMPORTANT - THESE ARE REQUIRED MANUAL STEPS: set calibrant image, config file and mask) → press "Run" → wait until finishes.
After calibration is set, the integration is done with calibrated geometry.
7. **If buffer subtraction is required** → **Set buffer** → choose buffer `.dat` and options (IMPORTANT - THESE ARE REQUIRED MANUAL STEPS: set q_min and q_max, this is the range to match tails to get the correct buffer scale for subtraction, not necessarily very large) in the wizard → **Apply**.
After buffer is set, automatic subtraction follows integration immediately for every new `.tif` file.
8. **Optional modeling / fits (right column, “Analysis”)** → pick a mode from the **drop-down** at the top of the **Analysis** group, then use the buttons below it to open the wizards and save options (GNOM / shapes / mixture, etc., depending on the mode). The chosen pipeline runs **automatically for every new `.tif`** once calibration is in place (and uses the live 1D curve after integration, or subtraction if you configured a buffer). The list entries are:
   - **Off** — no extra fit steps after integration (and subtraction if enabled).
   - **Monodisperse analysis: p(r)** — **fit_distances** (AUTORG + GNOM): fit vs data and pair-distance distribution **p(r)**.
   - **Monodisperse analysis: DAM** — same GNOM setup as **p(r)**, then **DAMMIF** (FAST mode) on the GNOM **.out**; adds **3D** preview of the bead model.
   - **Monodisperse analysis: primitives** — **fit_bodies** (BODIES; `--first` from in-process Guinier) for simple geometric **primitives**; shows curve comparison and **3D** for the best-fitting shape.
   - **Polydisperse analysis: d(r)** — **fit_sizes**: polydisperse size distribution **d(r)** (GNOM) and fit plot.
   - **Polydisperse analysis: mixture** — **fit_mixture** (MIXTURE): multi-component modeling; shows component and distribution previews.
9. The top panel. There are three buttons: "<", ">" and "Process". "<" and ">" allow you to navigate through the files processed in this section. Remember - these buttons don't trigger any processing, they just re-upload existing results. If some results are missing, the corresponding fields just remain empty. To run the processing for the current 2D image, press "Process" button whcih is equivalent to re-uploading the file.
10. **To quit** → close the window.

---

## Input and output files

All paths below are **inside your selected Watchdir** (the folder shown at the top as **Watchdir: ...**).

### Inputs (what the program consumes)

- **New detector images**: `Watchdir/*.tif` or `Watchdir/*.tiff`
  - If you drag-and-drop a TIFF from somewhere else, the app **copies it into** the watchdir and processes the copy.
- **Calibration inputs** (picked in the calibration wizard): files you choose in the UI (a calibrant image, config, mask, etc.). Their original locations are not changed.
- **Buffer subtraction inputs** (picked in the buffer wizard): buffer `.dat` you choose in the UI. Its original location is not changed.
- **fit_distances inputs**:
  - Manual run: the profile `.dat` you point to in the wizard.
  - Automatic pipeline run: uses the latest produced curve (integrated or subtracted) from the live pipeline.

### Outputs (where to find results)

- **Per-skill run logs (always written)**:
  - Latest skill run: `Watchdir/runs/latest/request.yml`, `result.yml`, `stdout.log`, `stderr.log`
    - Example skill folder names: `..._integrate`, `..._integrate_proxy`, `..._subtract`, `..._fit_distances`, `..._fit_dammif`, `..._fit_bodies`, `..._fit_sizes`, `..._fit_mixture`

- **Calibration (`calibrate`) outputs**: `Watchdir/calibration/`
  - This folder is used as the calibration skill `output_dir` (contains the produced integrator directory and plots/results from calibration).

- **Integration outputs**:
  - When **not calibrated yet** (proxy integrate): `Watchdir/averaged_proxy/` (from `integrate_proxy`)
  - When calibrated (normal integrate): `Watchdir/averaged/` (from `integrate`)
  - The latest integrated 1D curve path is also recorded in the skill `result.yml` under `integrated_1d`.

- **Subtraction (`subtract`) outputs**: `Watchdir/subtracted/`
  - Subtracted 1D curve path is in `result.yml` under `subtracted_1d`.

- **Analysis outputs (after integration / subtraction, when an Analysis mode is on)**  
  Fitting skills use a **per-sample subdirectory** under each skill folder: `Watchdir/<skill>/<stem>/`, where **`<stem>`** is the basename of the processed `.tif` / `.tiff` (no extension). Saved **wizard options** (shared across TIFFs) sit next to those folders as small config files, not inside `<stem>`.

  - **Monodisperse p(r)** (`fit_distances`): `Watchdir/fit_distances/<stem>/`
    - Wizard writes options once: `Watchdir/fit_distances/fit_distances.conf`
    - Typical artifacts: GNOM **.out**, summaries, and PNGs such as `<stem>_fits.png` (fit vs data) plus a companion **p(r)** plot. The skill `result.yml` records paths like `fit_vs_exp_png_path`, `best_pr_png_path`, `best_gnom_out_path`, `best_summary_path`, `fit_params_path`.

  - **Monodisperse DAM** (`fit_distances` then `fit_dammif`): same **`fit_distances`** layout as above, plus **`Watchdir/dammif/<stem>/`**
    - DAMMIF bead models, `dammif_fits.yml`, `dammif-*-1.cif`, and summary PNGs (e.g. `*_fits.png`) used for the **3D** preview.

  - **Monodisperse primitives** (`fit_bodies`): **`Watchdir/fit_bodies/<stem>/`**
    - Shape list saved once: `Watchdir/fit_bodies/fit_bodies.conf`
    - Typical artifacts: `bodies_fits.yml`, `bodies_fits.csv`, fit PNGs; the UI may plot **exp vs best model** from the CSV or fall back to a `*_fits.png`.

  - **Polydisperse d(r)** (`fit_sizes`): `Watchdir/fit_sizes/<stem>/`
    - Wizard writes options once: `Watchdir/fit_sizes/fit_sizes.conf`
    - Typical PNGs: `<stem>_fit_sizes_fits.png` (fit) and a `*_DR.png` (**d(r)**); `result.yml` includes `best_dr_png_path` and related GNOM paths.

  - **Polydisperse mixture** (`fit_mixture`): `Watchdir/mixture/<stem>/`
    - Liveview mixture YAML (wizard): `Watchdir/mixture/liveview_mixture.yml` (and optional pointer file `Watchdir/mixture/liveview_mixture_config.txt`)
    - Typical plots: `mixture_comparison_I_vs_q.png`, `mixture_distributions.png`, plus a results **CSV** (`results_csv_path` in `result.yml`).

---

## Rules the watcher follows (so you are not surprised)

- Only the **top-level** watch directory is watched — **not** nested folders.
- Only files that appear **after** the app started count as “new” (plus **moves** into the folder, and **overwrites** of an existing `.tif` / `.tiff` name). Old TIFFs already sitting there are **not** auto-queued until they are replaced or you drag-and-drop them to the middle column.
