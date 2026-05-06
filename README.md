# `autosaxs` package + `guisaxs` app + `guisaxs-skills` app

This project contains:

- **`autosaxs`**: a Python package for building reproducible SAXS processing pipelines (calibration → integration → subtraction → analysis → reports) usable from **Python** or from a **CLI**.
- **`guisaxs`**: a lightweight desktop GUI app built on top of `autosaxs` for interactive, file-driven processing (drag & drop TIFFs, run calibration, integrate buffer/sample, produce plots and subtracted curves).

---

## `autosaxs` (package)

### Purpose

`autosaxs` is the **data processing core**: it provides the computation and the public “skills” API used by both:

- **CLI users** (the `autosaxs` command), and
- **GUI users** (the `guisaxs` app).

Its main goal is to make common SAXS processing steps **scriptable, cacheable, and reproducible**, while staying convenient for interactive use.

### Main design choices

- **One public API for everything (“skills”)**: user-facing operations are implemented as Python functions with stable signatures in the `autosaxs.skill` package (`repos/autosaxs/skill/`). The CLI dispatches subcommands to those functions by introspecting signatures and docstrings.
- **Path expressions instead of “single file only”**: most skills accept a file/dir/glob expression and expand it in a consistent way (directories expand non-recursively; empty expansion is an error).
- **Cache-by-default**: when `use_cache=True`, skills may reuse outputs via a hidden cache file under the output directory (intended for fast re-runs during interactive work).
- **External science stack integration**: `pyFAI` is used for calibration/integration; several downstream steps rely on **ATSAS** being installed (see below).

### Requirements (ATSAS)

Some parts of the pipeline rely on ATSAS executables (e.g. `dammif`). On import, `autosaxs` checks that **ATSAS 3.2.1** is installed and available on `PATH` (by running `dammif -v`). If it is missing or a different version is found, importing `autosaxs` will raise a `RuntimeError`.

- **ATSAS download**: `https://www.embl-hamburg.de/biosaxs/download.html`

### Installation

#### Non-GUI (core + CLI only)

Install the package (includes the `autosaxs` CLI entry point):

```bash
python -m pip install "autosaxs @ git+http://hpc.nano.sfedu.ru:8080/mikhail/saxsprocessing.git@main"
```

Then check the CLI:

```bash
autosaxs --help
```

#### GUI-enabled (`guisaxs`)

Install with GUI extras (adds `customtkinter` and `tkinterdnd2`):

```bash
python -m pip install "autosaxs[gui] @ git+http://hpc.nano.sfedu.ru:8080/mikhail/saxsprocessing.git@main"
```

This also installs the `guisaxs` console entry point (see below).

---

## `guisaxs` (app)

### Purpose

`guisaxs` is an **interactive desktop app** for processing SAXS data by dropping files into the UI:

- calibrate from a **calibrant TIFF** (optionally using a mask),
- integrate a **buffer TIFF** to a 1D curve,
- integrate one or more **sample TIFFs** to 1D curves,
- automatically generate **subtracted** curves (sample − last buffer),
- save standard plots for 2D images and 1D curves into the working directory.

It is designed to be “thin”: the GUI coordinates user input, shows plots, and delegates computation to the same underlying `autosaxs` pipeline components used by the CLI.

### Launch

After installing with the GUI extra, start the application:

```bash
guisaxs
```

On startup you will be prompted to choose a **working directory**. The directory must be **empty**; `guisaxs` writes all outputs there (including `config.yml`).

If GUI dependencies are missing, `guisaxs` exits with:

```text
GUI dependencies are not installed. Install with: pip install "autosaxs[gui]"
```

### Layout

The main window is split into a left **Control Panel** and a right **Visualization** area:

- **Left: Control Panel**
  - **Drag & drop zones**:
    - `Calibrant Image` (TIFF)
    - `Mask File (Optional)` (`.npy`, `.txt`, or `.msk`)
    - `Buffer Image` (TIFF)
    - `Sample Image(s)` (one or many TIFFs)
  - **Calibration Parameters** (editable entry + slider for each):
    - wavelength (Å)
    - detector distance (mm)
    - pixel size (mm)
    - beam center X/Y (px)
    - detector tilt (rad)
    - tilt plane rotation (rad)
  - **Apply Calibration** button

- **Right: Visualization tabs**
  - **2D Images**: shows the dropped TIFFs as 2D plots (and saves PNGs).
  - **1D Curves**: shows integrated/subtracted 1D curves (and saves PNGs).

- **Bottom: Status bar**
  - Shows progress/success/error messages from background calibration/processing threads.

### How to use (tested scenarios)

The GUI has automated “headless” tests that drive the real UI (no pixel assertions) in `repos/tests/test_guisaxs.py`. The following workflows are explicitly tested against validation reference data.

#### Scenario A (calibrant → mask → apply calibration → buffer → sample)

- Drop a **calibrant TIFF** into `Calibrant Image`
- Drop a **mask** into `Mask File (Optional)`
- Click **Apply Calibration** and wait for calibration to finish
- Drop **buffer TIFF** into `Buffer Image` and wait until the integrated curve appears in the working directory
- Drop **sample TIFF** into `Sample Image(s)` and wait for:
  - `int_<sample>.dat` (integrated sample)
  - `sub_<sample>.dat` (subtracted curve using the most recent buffer)

#### Scenario B (mask → calibrant → apply calibration → buffer → sample)

Same as Scenario A, but the first two drops are swapped:

- Drop **mask** first, then the **calibrant TIFF**

#### Practical note: buffer then sample

The tested workflow intentionally waits for the buffer integration output to exist before dropping the sample. Doing buffer and sample “at once” can start overlapping workers and may cause issues with concurrent access to a shared integrator. In practice: **drop buffer, wait for `int_<buffer>.dat`, then drop sample(s)**.

### Outputs (working directory)

`guisaxs` writes its artifacts into the chosen working directory, including:

- `config.yml` (current parameters)
- integrated 1D curves like `int_<name>.dat`
- subtracted curves like `sub_<name>.dat`
- plots saved by the 2D and 1D tabs (PNG)

### Headless / CI usage (xvfb)

The GUI tests require a display; on CI you can run them under Xvfb:

```bash
xvfb-run -a python -m pytest repos/tests/test_guisaxs.py
```

---

## `guisaxs-skills` (app)

`guisaxs-skills` is a newer GUI concept: a single-window **skill console** that runs `autosaxs` skills (calibrate/integrate/subtract/analysis/…) via the `autosaxs` CLI in an **isolated process**, streams logs live, and then shows the returned artifact paths with basic previews.

- **Docs**: see `repos/guisaxs_skills/README.md`
- **Launch (dev)**: `python -m guisaxs_skills`

---

## `autosaxs` skills reference

This section documents the public *skills* exposed by the `autosaxs` package.

Skills are Python functions in the `autosaxs.skill` package (`repos/autosaxs/skill/`) with a fixed signature designed to be callable both from Python and from the `autosaxs` CLI.

### CLI vs Python (how commands are wired)

The `autosaxs` command dispatches subcommands to the corresponding skill functions by introspecting their signatures. In practice:

- Run a skill from the CLI as `autosaxs <command> ...`.
- Every skill supports `--output-dir <path>` (maps to the skill's `output_dir` argument, default: `.`).
- Every skill supports caching by default; use `--no-cache` to disable it (maps to `use_cache=False` in Python).
- Positional arguments in the CLI match the skill signature order.
- Keyword options use `--kebab-case` names (underscores become `-`).

### Path expansion (important API behavior)

Most skills take a **path expression** rather than a strict “single file”:

- A file path is used as-is.
- A directory expands to matching files (non-recursive):
  - 2D inputs: `*.tif`
  - 1D inputs: `*.dat`
- A glob expression is allowed (including `**`); results are sorted, and **empty expansion is an error**.

Note: `autosaxs integrate` accepts either a single path expression **or** multiple image paths on the CLI (the CLI passes a list; the skill normalizes it).

### Caching (enabled by default)

- When `use_cache=True`, a skill may write/read a hidden `.cache` YAML file inside its output directory.
- Re-running with the same inputs and relevant options can reuse previously generated output paths if the files still exist and are recent enough (output-integrity check).
- On cache hits, the returned dict includes `from_cache=True` in addition to the usual output path keys.

---

## `calibrate`

Calibrate detector geometry using a calibration image and a config (ring-analysis + geometry refinement). This is a prerequisite for `integrate`.

### Arguments

- `calib_image` (str): Path to the calibration image (e.g. TIFF) used for ring analysis.
- `config_path` (str): Path to the autosaxs calibration config file. The config must include data required by the ring analysis and detector geometry refinement.
- `output_dir` (str, default `.`): Directory where results are written.
- `mask` (str | None, default `None`): Optional path to a mask used during ring analysis. Supports .txt (NuPy format), .msk (Fit2d)
- `mask_mode` (str, default `"f"`): Mask mode selector. One of `f/from_file`, `a/auto`, `c/combined`.
- `calibrant` (str, default `"AgBh"`): Calibrant name (must be in `pyFAI.calibrant.ALL_CALIBRANTS`).
- `use_cache` (bool, default `True`): Enable/disable caching for this skill run.

Important constraints:

- If `mask_mode` is `f/from_file` or `c/combined`, `mask` **must** be provided (the skill raises `ValueError` otherwise).

### Returns

`dict[str, str]` with these output path roles:

- `integrator_dir`: Directory containing the calibrated integrator (used by `integrate`).
- `refined_path`: Path to the refined calibration YAML.
- `calibration_plots_dir`: Directory containing calibration plots.
- `calibration_curve_plot_path`: Path to the calibration q/I curve plot (PNG).
- `calibration_mask_path`: Path to the calibration mask visualization (PNG).

### Python usage

```python
from autosaxs.skill import calibrate

out = calibrate(
    calib_image="AgBh.tif",
    config_path="config_autocalib.yml",
    output_dir="calibration",
    mask="mask.msk",
    mask_mode="f",
    calibrant="AgBh",
    use_cache=True,
)

print(out["integrator_dir"])
print(out["refined_path"])
```

### CLI usage

```bash
autosaxs calibrate AgBh.tif config_autocalib.yml --output-dir calibration --mask mask.msk
```

---

## `integrate`

Integrate 2D SAXS images to 1D curves (q, I, sigma) using a calibrated integrator produced by `calibrate`.

### Arguments

- `images` (str): Image path expression. Can be:
  - a single `.tif` file path
  - a directory (expands to `*.tif`, non-recursive)
  - a glob expression
  - a comma-separated list of file paths (e.g. from multi-file drag & drop)
- `integrator_dir` (str): Path to the calibrated integrator directory (from `calibrate`).
- `output_dir` (str, default `.`): Directory where integrated curves are written.
- `npt` (int, default `1000`): Number of points in the output q grid.
- `use_cache` (bool, default `True`): Enable/disable caching for this skill run.

### Returns

`dict[str, str | list[str]]` with:

- `integrated_1d`: List of paths to integrated 1D `.dat` curves (one per input image).

### Python usage

```python
from autosaxs.skill import integrate

out = integrate(
    images="/data/sample_*.tif",
    integrator_dir="calibration/integrator",
    output_dir="integration",
    npt=1000,
    use_cache=True,
)

print(out["integrated_1d"])
```

### CLI usage

```bash
autosaxs integrate "/data/sample_01.tif, /data/sample_02.tif" calibration/integrator       --output-dir integration --npt 1000
```

---

## `integrate_proxy`

Integrate 2D TIFF image(s) to a 1D curve **without detector calibration**, using radial averaging in pixel-radius space.

This is intended for quick-look / debugging when you don’t have a calibrated integrator yet. The output `.dat` stores metadata indicating the x-axis is `r_px` (pixels), not physical q.

### Arguments

- `image` (str): 2D image path expression. Can be:
  - a single `.tif` file path
  - a directory (expands to `*.tif`, non-recursive)
  - a glob expression (including `**`)
- `output_dir` (str, default `.`): Directory where integrated curves are written.
- `mask` (str | None, default `None`): Optional mask path; same shape as the image. (`pyFAI` convention: masked pixels are excluded.)
- `cy` (float | None, default `None`): Optional beam center y in pixels. Must be set together with `cx`.
- `cx` (float | None, default `None`): Optional beam center x in pixels. Must be set together with `cy`.
- `npt` (int, default `1000`): Number of points in the output x grid.
- `use_cache` (bool, default `True`): Enable/disable caching for this skill run.

Notes:

- If `cy/cx` are not provided, the skill **estimates** the center by radial-symmetry optimization and also writes a center diagnostic plot `*_center.png` into `output_dir`.
- If center estimation fails for an input, that item is skipped and the skill may return an empty list for `integrated_1d`.

### Returns

`dict[str, str | list[str]]` with:

- `integrated_1d`: Path (or list of paths, if `image` is a directory) to integrated 1D `.dat` curves.

### Python usage

```python
from autosaxs.skill import integrate_proxy

out = integrate_proxy(
    image="raw/sample_01.tif",
    output_dir="integration_proxy",
    mask="mask.msk",
    npt=1000,
    use_cache=True,
)

print(out["integrated_1d"])
```

### CLI usage

```bash
autosaxs integrate-proxy raw/sample_01.tif --output-dir integration_proxy --mask mask.msk --npt 1000
```

---

## `subtract`

Subtract a buffer curve from a sample 1D profile. Scaling uses either `point_match` (default)
or legacy `match_tail`, optionally restricted to a q window (`q_min` / `q_max`).

### Arguments

- `sample_1d` (str): Sample path expression (file/dir/glob). Directories expand to `*.dat` (non-recursive).
- `buffer_1d` (str): Path to the buffer 1D `.dat` curve (must be an existing file).
- `output_dir` (str, default `.`): Directory where subtraction outputs are written.
- `method` (str, default `"point_match"`): `point_match` or `match_tail`.
- `q_min` (float | None, default `None`): Lower bound of q-range for fitting/scaling.
- `q_max` (float | None, default `None`): Upper bound of q-range; for `point_match` the match uses this as q intersect (upper edge of the window).
- `sample_form` / `buffer_form` (str): For `point_match` only — each is `linear`, `Porod`, or `Porod-plus-linear`.
- `point_match_factor` (float, default `0.995`): For `point_match`, scale satisfies `point_match_factor * I_sample_fit(q_max) = scale * I_buffer_fit(q_max)`.
- `scaling_factor` (float | None, default `None`): If provided, overrides automatic scaling and uses this factor directly (must be finite and > 0).
- `use_cache` (bool, default `True`): Enable/disable caching for this skill run.

Important constraint:

- If you set `q_max`, you must also set `q_min` (otherwise the skill raises `ValueError`).

### Returns

`dict[str, str]` with:

- `subtracted_1d`: Path to the subtracted curve `.dat`.
- `diff_plot_path`: Path to a diff plot PNG.
- `diff_log_plot_path`: Path to a diff plot PNG with log(I) vs q.
- `sub_plot_path`: Path to a subtracted curve plot PNG.

### Python usage

```python
from autosaxs.skill import subtract

out = subtract(
    sample_1d="integration/int_sample_01.dat",
    buffer_1d="integration/int_buffer.dat",
    output_dir="subtracted",
    method="point_match",
    q_min=4.0,
    q_max=6.0,
    use_cache=True,
)

print(out["subtracted_1d"])
```

### CLI usage

```bash
autosaxs subtract integration/int_sample_01.dat integration/int_buffer.dat       --output-dir subtracted --method point_match --q-min 4.0 --q-max 6.0
```

---

## `plot`

Generate standard plots for a 1D curve:

- Guinier plot (log(I) vs q^2)
- Kratky plot (I*q^2 vs q)
- log-log plot (log(I) vs log(q))

Also writes a Guinier `.dat` file (ln(I) vs q²) used downstream.

### Arguments

- `profile` (str): 1D path expression (file/dir/glob). Directories expand to `*.dat` (non-recursive).
- `output_dir` (str, default `.`): Directory where plot files are written.
- `guinier_q_min` (float | None, default `None`): Lower q bound for selecting Guinier range (enables `guinier_dat_path`).
- `guinier_q_max` (float | None, default `None`): Upper q bound for selecting Guinier range.
- `use_cache` (bool, default `True`): Enable/disable caching for this skill run.

Important constraint:

- If you set `guinier_q_max`, you must also set `guinier_q_min` (otherwise the skill raises `ValueError`).

### Returns

`dict[str, str]` with:

- `guinier_plot_path`: Path to the Guinier PNG.
- `kratky_plot_path`: Path to the Kratky PNG.
- `loglog_plot_path`: Path to the log-log PNG.
- `guinier_dat_path`: Path to the Guinier `.dat` (q², ln(I)) written by the skill (always written; independent of `guinier_q_min/max`).

### Python usage

```python
from autosaxs.skill import plot

out = plot(
    profile="subtracted/sub_sample_01.dat",
    output_dir="plots",
    guinier_q_min=0.01,
    guinier_q_max=0.05,
    use_cache=True,
)

print(out["guinier_dat_path"])
```

### CLI usage

```bash
autosaxs plot subtracted/sub_sample_01.dat --output-dir plots --guinier-q-min 0.01 --guinier-q-max 0.05
```

---

## `plot_2d`

Render one 2D SAXS TIFF image (or all `.tif` images in a directory) to PNG using log-intensity scaling.

### Arguments

- `image` (str): 2D path expression (file/dir/glob). Directories expand to `*.tif` (non-recursive).
- `output_dir` (str, default `.`): Directory where PNG(s) are written.
- `use_cache` (bool, default `True`): Enable/disable caching for this skill run.

### Returns

`dict[str, str | list[str]]` with:

- `plot_2d_png`: Path (or list of paths, if `image` is a directory) to generated PNG(s).

### Python usage

```python
from autosaxs.skill import plot_2d

out = plot_2d(
    image="raw/sample_01.tif",
    output_dir="plots_2d",
    use_cache=True,
)

print(out["plot_2d_png"])
```

### CLI usage

```bash
autosaxs plot-2d raw/sample_01.tif --output-dir plots_2d
```

---

## `guinier_analysis`

Run Guinier analysis on a 1D profile (including multiple strategies such as first-interval fits and an adaptive choice). The skill writes:

- a text results file
- an ATSAS-format `.dat` file for downstream tools
- a YAML file describing the chosen Guinier region parameters

### Arguments

- `profile` (str): 1D path expression (file/dir/glob). Directories expand to `*.dat` (non-recursive).
- `output_dir` (str, default `.`): Directory where analysis outputs are written.
- `use_cache` (bool, default `True`): Enable/disable caching for this skill run.

### Returns

`dict[str, str]` with:

- `results_path`: Path to the results text file.
- `atsas_dat_path`: Path to the ATSAS-format `.dat` file.
- `guinier_region_path`: Path to the chosen Guinier region YAML.

### Python usage

```python
from autosaxs.skill import guinier_analysis

out = guinier_analysis(
    profile="subtracted/sub_sample_01.dat",
    output_dir="guinier",
    use_cache=True,
)

print(out["guinier_region_path"])
```

### CLI usage

```bash
autosaxs guinier-analysis subtracted/sub_sample_01.dat --output-dir guinier
```

---

## `fit_distances`

Run ATSAS DATGNOM to obtain a pair distance distribution function \(p(r)\) for a monodisperse system from a 1D SAXS curve.

### Arguments

- `profile` (str): 1D path expression (file/dir/glob). Directories expand to `*.dat` (non-recursive).
- `output_dir` (str, default `.`): Directory where the GNOM outputs are written (one subdirectory per input profile).
- `rg_nm` (float | None, default `None`): Expected Rg in nm. If omitted, taken from AUTORG when possible, else from Guinier search.
- `first` (int | None, default `None`): DATGNOM `--first`. If omitted, taken from AUTORG Guinier interval when possible. If set with `last`, runs one fit. If set alone, `last` is auto-searched unless AUTORG succeeded and `last` is omitted (then DATGNOM runs without `--last`). If omitted and AUTORG fails or gives no interval, `first` is auto-searched.
- `last` (int | None, default `None`): DATGNOM `--last`. Same pairing rules as `first`; if set alone, `first` is auto-searched. Omitted with successful AUTORG implies a single DATGNOM run without `--last`.
- `smooth` (float | None, default `None`): DATGNOM `--smooth`. If set, that value is used and smoothness is not searched. If omitted during auto-search, trials use smoothness `2.0`. In full manual mode (`first` and `last` both set), omitted means do not pass `--smooth`.
- `use_cache` (bool, default `True`): Enable/disable caching for this skill run.

### Returns

`dict[str, str | list[str]]` with:

- `output_subdir`: The per-sample output directory used for this profile.
- `gnom_out_paths`: List of DATGNOM `.out` paths written for this profile (typically a single “best” `.out`).
- `best_gnom_out_path`: Path to the selected “best” DATGNOM `.out`.
- `best_summary_path`: Path to a YAML summary of candidate runs and the selected parameters.
- `fit_params_path`: Path to a YAML file containing the fit parameters used for the final run.
- `best_symlink_out_path`: Best-effort symlink path to the selected `.out` (may be missing on some filesystems).
- `fits_csv_path`: Path to a CSV containing candidate scores/metadata.
- `fit_vs_exp_png_path` / `fit_vs_exp_png_error`: Fit-vs-experiment plot output or error message.
- `best_pr_png_path` / `best_pr_png_error`: \(p(r)\) plot output or error message.

### Python usage

```python
from autosaxs.skill import fit_distances

out = fit_distances(
    profile="subtracted/sub_sample_01.dat",
    output_dir="distances",
    use_cache=True,
)

print(out["best_gnom_out_path"])
```

### CLI usage

```bash
autosaxs fit_distances subtracted/sub_sample_01.dat --output-dir distances
```

---

## `fit_sizes`

Run ATSAS GNOM (system=1/5) to obtain a size distribution function \(D(R)\) for a polydisperse system from a 1D SAXS curve.

### Arguments

- `profile` (str): 1D path expression (file/dir/glob). Directories expand to `*.dat` (non-recursive).
- `output_dir` (str, default `.`): Output directory (one subdirectory per input profile).
- `shape` (str, default `spheres`): Polydisperse system model. Options:
    - `spheres`: GNOM `--system=1` (volume distribution for solid spheres).
    - `rods`: GNOM `--system=5` (length distribution for long cylinders). Requires `rad56_nm` (cylinder radius).
    - `ellipsoids`: accepted for API compatibility but **not supported by GNOM command-line** (GNOM system 2 is
      interactive-only). The skill will raise a clear error if selected.
- `rg_nm` (float | None): Expected Rg in nm; if omitted, inferred by AUTORG when possible, else via Guinier fit.
- `rmin_nm` (float | None, default `0.0`): GNOM `--rmin` (nm). If None, GNOM default is used.
- `rmax_nm` (float | None): GNOM `--rmax` (nm). Required by GNOM; if omitted, the skill searches candidates.
- `rad56_nm` (float | None): GNOM `--rad56` for `shape=rods` (nm cylinder radius). Ignored for spheres.
- `first`/`last` (int | None): GNOM `--first`/`--last` data-point indices (1-based).
- `alpha` (float | None, default `0.0`): GNOM `--alpha`. Use 0.0 (default) for automatic alpha search.
- `nr` (int | None): GNOM `--nr` (number of real-space points). If omitted, GNOM chooses automatically.
- `use_cache` (bool, default `True`): Enable/disable caching for this skill run.

### Returns

`dict[str, str | list[str]]` with:

- `output_subdir`: The per-sample output directory used for this profile.
- `gnom_out_paths`: List of GNOM `.out` paths written for this profile (typically a single “best” `.out`).
- `best_gnom_out_path`: Path to the selected “best” GNOM `.out`.
- `best_summary_path`: Path to a YAML summary of candidate runs and the selected parameters.
- `fit_params_path`: Path to a YAML file containing the fit parameters used for the final run.
- `best_symlink_out_path`: Best-effort symlink path to the selected `.out` (may be missing on some filesystems).
- `fits_csv_path`: Path to a CSV containing candidate scores/metadata.
- `fit_vs_exp_png_path` / `fit_vs_exp_png_error`: Fit-vs-experiment plot output or error message.
- `best_dr_png_path` / `best_dr_png_error`: \(D(R)\) plot output or error message.
- `dr_csv_path`: Path to a CSV export of \(D(R)\) (if successfully parsed).

### Python usage

```python
from autosaxs.skill import fit_sizes

out = fit_sizes(
    profile="subtracted/sub_sample_01.dat",
    output_dir="sizes",
    shape="spheres",
    use_cache=True,
)

print(out["best_gnom_out_path"])
```

### CLI usage

```bash
autosaxs fit-sizes subtracted/sub_sample_01.dat --output-dir sizes --shape spheres
```

---

## `fit_mixture`

Run MIXTURE fits on a 1D subtracted curve, select the best model by BIC, and write a comparison plot, size distribution plot, and results CSV.

### Arguments

- `profile` (str): 1D path expression (file/dir/glob). Directories expand to `*.dat` (non-recursive).
- `output_dir` (str, default `.`): Directory where the MIXTURE outputs are written.
- `config_path` (str | None, default `None`): Path to the autosaxs YAML config (must include a `mixture` section). Required for this skill.
- `q_min_nm` (float | None, default `None`): Optional q minimum bound (nm^-1) for the fitting range.
- `q_max_nm` (float | None, default `None`): Optional q maximum bound (nm^-1) for the fitting range.
- `use_cache` (bool, default `True`): Enable/disable caching for this skill run.

Important constraint:

- If you set `q_max_nm`, you must also set `q_min_nm` (otherwise the skill raises `ValueError`).

### Returns

`dict[str, str]` with:

- `output_subdir`: The subdirectory that contains MIXTURE outputs.
- `comparison_path`: Path to the MIXTURE comparison plot.
- `distributions_path`: Path to the MIXTURE size distributions plot.
- `results_csv_path`: Path to the MIXTURE results CSV.

### Python usage

```python
from autosaxs.skill import fit_mixture

out = fit_mixture(
    profile="subtracted/sub_sample_01.dat",
    output_dir="mixture",
    config_path="config_autosaxs.yml",
    q_min_nm=0.8,
    q_max_nm=2.5,
    use_cache=True,
)

print(out["results_csv_path"])
```

### CLI usage

```bash
autosaxs fit-mixture subtracted/sub_sample_01.dat --output-dir mixture --config-path config_autosaxs.yml       --q-min-nm 0.8 --q-max-nm 2.5
```

---

## `fit_bodies`

Run ATSAS `bodies` fits for multiple candidate shapes on a 1D profile, exporting fit files (FIR, PNG, YAML, CSV) and a comparison figure.

### Arguments

- `profile` (str): 1D path expression (file/dir/glob). Directories expand to `*.dat` (non-recursive).
- `output_dir` (str, default `.`): Directory where `bodies` outputs are written.
- `shapes` (list[str] | None, default `None`): Subset of body model names to fit (`BODIES_SHAPES_LIST`). `None` or empty means fit **all** models (single `bodies` invocation). A non-empty list runs `bodies --body=...` per shape.
- `first` (int | None, default `None`): Passed to `bodies` as `--first` (1-based data point index). Omitted when `None`.
- `last` (int | None, default `None`): Passed to `bodies` as `--last` (1-based data point index). Omitted when `None`.
- `use_cache` (bool, default `True`): Enable/disable caching for this skill run.

### Returns

`dict[str, str]` with:

- `output_subdir`: Directory containing the exported `bodies` fit artifacts.

The directory typically contains multiple per-shape FIT files plus aggregated `bodies_fits.yml` and `bodies_fits.csv` if any shapes successfully fit.

### Python usage

```python
from autosaxs.skill import fit_bodies

out = fit_bodies(
    profile="subtracted/sub_sample_01.dat",
    output_dir="bodies",
    shapes=["cylinder", "ellipsoid"],
    first=10,
    last=120,
    use_cache=True,
)

print(out["output_subdir"])
```

### CLI usage

```bash
autosaxs fit_bodies subtracted/sub_sample_01.dat --output-dir bodies --shapes cylinder ellipsoid --first 10 --last 120
```

---

## `fit_dammif`

Run ATSAS `dammif` (ab initio shape reconstruction) on a 1D profile. If a GNOM output file is available, you can provide it; otherwise the profile is used.

### Arguments

- `profile` (str): 1D path expression (file/dir/glob). Directories expand to `*.dat` (non-recursive).
- `output_dir` (str, default `.`): Directory where `dammif` outputs are written.
- `gnom_path` (str | None, default `None`): Optional path to a GNOM `.out` file. If provided, `dammif` uses it.
- `dammif_reps_num` (int, default `1`): Number of independent DAMMIF runs (replicas) to execute.
- `use_cache` (bool, default `True`): Enable/disable caching for this skill run.

### Returns

`dict[str, str]` with:

- `output_subdir`: Directory containing `dammif` fit artifacts (FIR/CIF and summary files).

### Python usage

```python
from autosaxs.skill import fit_dammif

out = fit_dammif(
    profile="subtracted/sub_sample_01.dat",
    output_dir="dammif",
    gnom_path="guinier/sample_01_gnom.out",
    dammif_reps_num=1,
    use_cache=True,
)

print(out["output_subdir"])
```

### CLI usage

```bash
autosaxs fit_dammif subtracted/sub_sample_01.dat --output-dir dammif --gnom-path guinier/sample_01_gnom.out --dammif-reps-num 1
```

---

## `report_individual`

Build a per-sample PDF report from an existing pipeline directory. The skill scans `directory` for paths matching the provided `basename` and then assembles the report sections.

### Arguments

- `directory` (str): Path to the existing pipeline output directory (the place where intermediate results live).
- `basename` (str): Sample identifier used to match intermediate artifacts within `directory`.
- `output_dir` (str, default `.`): Directory where the PDF report is written.
- `output_path` (str | None, default `None`): Optional explicit output PDF path. If not provided, defaults to `<output_dir>/<basename>_report.pdf`.
- `use_cache` (bool, default `True`): Present for CLI parity; report generation does not use caching.

### Returns

`dict[str, Any]` with:

- `report_pdf_path`: Path to the generated PDF.

### Python usage

```python
from autosaxs.skill import report_individual

out = report_individual(
    directory="pipeline_out",
    basename="sample_01",
    output_dir="reports",
)

print(out["report_pdf_path"])
```

### CLI usage

```bash
autosaxs report_individual pipeline_out sample_01 --output-dir reports
```

---

## `report_summary`

Build a summary PDF report for all samples found inside an existing pipeline directory. The skill discovers samples and combines plots/tables where data exists.

### Arguments

- `directory` (str): Path to the existing pipeline output directory.
- `output_dir` (str, default `.`): Directory where the summary PDF is written.
- `output_path` (str | None, default `None`): Optional explicit output PDF path. If not provided, defaults to `<output_dir>/summary_report.pdf`.
- `use_cache` (bool, default `True`): Present for CLI parity; report generation does not use caching.

### Returns

`dict[str, Any]` with:

- `report_pdf_path`: Path to the generated summary PDF.

### Python usage

```python
from autosaxs.skill import report_summary

out = report_summary(
    directory="pipeline_out",
    output_dir="reports",
)

print(out["report_pdf_path"])
```

### CLI usage

```bash
autosaxs report_summary pipeline_out --output-dir reports
```

---
