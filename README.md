# `autosaxs` package + `guisaxs-skills` app + `guisaxs-liveview` app

This project contains:

- **`autosaxs`**: a Python package for building reproducible SAXS processing pipelines (calibration → integration → subtraction → analysis → reports) usable from **Python** or from a **CLI**.
- **`guisaxs-skills`**: a PyQt5 desktop GUI that acts as a strict interface to `autosaxs` skills (discover skills, generate forms from signatures, run via CLI in an isolated process, inspect artifacts and previews).
- **`guisaxs-liveview`**: a PyQt5 live, queued processing GUI that watches a directory for new `.tif/.tiff`, runs calibration/integration/subtraction, and optionally arms monodisperse or polydisperse analysis chains for each new image.

---

## `autosaxs` (package)

### Purpose

`autosaxs` is the **data processing core**: it provides the computation and the public “skills” API used by both:

- **CLI users** (the `autosaxs` command), and
- **GUI users** (the `guisaxs-skills` and `guisaxs-liveview` apps).

Its main goal is to make common SAXS processing steps **scriptable, cacheable, and reproducible**, while staying convenient for interactive use.

### Main design choices

- **One public API for everything (“skills”)**: user-facing operations are implemented as Python functions with stable signatures in the `autosaxs.skill` package (`repos/src/autosaxs/skill/`). The CLI dispatches subcommands to those functions by introspecting signatures and docstrings.
- **Path expressions instead of “single file only”**: most skills accept a file/dir/glob expression and expand it in a consistent way (directories expand non-recursively; empty expansion is an error).
- **Optional caching**: by default, skills run with `use_cache=False`. When `use_cache=True`, skills may reuse outputs via a hidden cache file under the output directory (intended for fast re-runs during interactive work).
- **External science stack integration**: `pyFAI` is used for calibration/integration; several downstream steps rely on **ATSAS** being installed (see below).

### Requirements (ATSAS)

Some parts of the pipeline rely on ATSAS executables (e.g. `dammif`). On import, `autosaxs` checks that **ATSAS 3.2.1** is installed and available on `PATH` (by running `dammif -v`). If it is missing or a different version is found, importing `autosaxs` will raise a `RuntimeError`.

- **ATSAS download**: `https://www.embl-hamburg.de/biosaxs/download.html`

### Installation

#### Non-GUI (core + CLI only)

Install the package (includes the `autosaxs` CLI entry point):

```bash
python -m pip install "autosaxs @ git+https://github.com/MikhailLifar/autoSAXS.git"
```

Then check the CLI:

```bash
autosaxs --help
```

Helper subcommands ship files from the package into a directory of your choice:

- `autosaxs get-readme` — write the generated skills README (`README.md`) into `--output-dir`
- `autosaxs get-skills` — replace the entire `saxs-processing/` directory under `--output-dir`, then write a SAXS orchestrator `SKILL.md` plus nested leaf `<name>/<name>.md` procedure docs (from skill docstrings; not nested `SKILL.md`); subskills are linked from the orchestrator catalog
- `autosaxs get-default-config` — copy the bundled default `config_base.conf` (skill-keyed YAML) into `--output-dir` for optional overrides; skills use bundled defaults when `--conf` is omitted

Example:

```bash
autosaxs get-default-config -o .
```

Upgrade an existing install from git `main`:

```bash
autosaxs -U
```

#### GUI-enabled apps (`guisaxs-skills`, `guisaxs-liveview`)

Install with GUI extras (adds **PyQt5**, `watchdog`, and legacy CustomTkinter deps still used by `autosaxs.pipeline.gui`):

```bash
python -m pip install "autosaxs[gui] @ git+https://github.com/MikhailLifar/autoSAXS.git"
```

This also installs the `guisaxs-skills` and `guisaxs-liveview` console entry points (see below).

---

## `guisaxs-skills` (app)

### Purpose

`guisaxs-skills` is a single-window **PyQt5** desktop GUI that acts as a **strict interface to `autosaxs` skills**:

- discovers skills from the public `autosaxs.skill` API,
- builds a parameter form from each skill’s signature,
- runs skills via the `autosaxs` CLI in an **isolated process**,
- shows produced artifact paths and previews (images + common scientific formats).

### Launch

After installing with the GUI extra, start the application:

```bash
guisaxs-skills
```

On startup the working directory is the process **current working directory** (`cwd`). Use **File → Open working directory…** to switch. The directory may be non-empty; the app can warn about potential overwrites and about cache behavior.

Requires the `[gui]` extra (PyQt5 and related dependencies).

### Layout

The main window is split into three columns:

- **Left: Skill catalog**
  - Lists available skills discovered from `autosaxs.skill` (report skills excluded).
  - Selecting a skill opens its form in the middle column.

- **Middle: Skill form + run controls + logs**
  - Skill header with a **?** help button (full docstring).
  - `Inputs` and `Options` groups generated from the skill signature.
  - **Run**, **Cancel**, **Copy CLI**, and run-state label.
  - Live stdout/stderr log view.

- **Right: Preview + artifacts**
  - Preview panel (top): images and rendered previews for `.dat`, `.tif/.tiff`, `.csv`, etc.
  - Artifact list (bottom): `key=value` paths parsed from CLI output.

**File → Open working directory…** switches the session to another folder.

### How to use (typical workflow)

- Select a skill in the catalog (e.g. `calibrate`, `integrate`, `subtract`, `fit_distances`, …)
- Provide required positional inputs in `Inputs` (supports drag & drop / browse / manual path expressions)
- Adjust `Options` (notably `output_dir` and `use_cache`; caching is opt-in — the GUI defaults to `--no-cache`)
- Optionally enable **Copy inputs into working directory** for files outside the workdir
- Click **Run** (or press Enter) and follow logs
- Inspect produced paths in the artifact list and click for previews

### Outputs (working directory)

`guisaxs-skills` writes its artifacts into the chosen working directory, including:

- `runs/latest/request.yml`, `stdout.log`, `stderr.log`, `result.yml` (traceability for the latest run)
- skill outputs under the configured `output_dir` (typically recommended subfolders like `calibration/`, `averaged/`, `subtracted/`, …)

---

## `guisaxs-liveview` (app)

### Purpose

`guisaxs-liveview` is a **PyQt5** desktop GUI for **live, queued processing** of incoming SAXS detector images:

- watches a directory for new `.tif/.tiff` files (and also supports drag & drop onto the middle **2D** panel),
- maintains a processing **queue** and handles files sequentially,
- after calibration (and optional buffer subtraction) continuously updates 1D plots in the middle column,
- optionally **arms** monodisperse or polydisperse analysis chains (separate windows) for each new TIFF while those windows stay open.

Implementation lives in `guisaxs_skills.liveview`; `guisaxs-liveview` is a thin launcher entry point.

### Launch

After installing with the GUI extra, start the application:

```bash
guisaxs-liveview
```

The app opens on the process **current working directory** (`cwd`) when it is writable. Use **File → Open watch directory…** to switch folders.

Full in-app documentation: **Help → guisaxs-liveview Help…**

Upgrade from the app via **Update → Update to latest version…**, or run `autosaxs -U` / reinstall `autosaxs[gui]`.

Bundled help HTML (for developers) lives under `autosaxs/resources/help/guisaxs_liveview/` (`manifest.yaml`, `html/`, `style/help.css`); edit and reinstall — no separate build step.

### Layout

Below the menu bar, a **Watchdir** line shows the active folder (tooltip = full path). The main area is three columns (~1 : 3 : 1 width):

- **Left: Calibration + buffer**
  - **Set calibration** / **Reset** — wizard for `calibrate`, preview, and refined-parameter table.
  - **Set buffer** / **Reset** — wizard for buffer `.dat` and subtraction q-range.

- **Middle: Live view**
  - Queue status and progress.
  - **2D** panel — latest TIFF; drop new `.tif/.tiff` here.
  - **1D** plots — integrated curve, or sample/buffer + subtracted pair when subtraction is enabled.
  - Session history: **<** **>** **Process** to browse or re-enqueue prior files.

- **Right: Analysis + log**
  - **Analysis** toolbar with two icon buttons:
    - **Monodisperse** — opens a separate wizard window (Guinier → GNOM p(r) → optional BODIES/DAMMIF/DENSS).
    - **Polydisperse** — opens a separate window (Guinier → D(R) → optional McSAS / mixture).
  - While a window is open, that chain is **armed** for new TIFFs; closing the window **disarms** it. Both may be open at once (independent pipelines and output trees).
  - Live log with **Full** (skill + app) and **App** tabs.

**Menu bar:**

- **File** — open watch directory; switch **flat** (top-level TIFFs only, outputs under watchdir) vs **tree** (recursive TIFF discovery, outputs beside each TIFF).
- **Update** — upgrade `autosaxs[gui]` from git `main` (or run `autosaxs -U`).
- **Help** — bundled HTML guide and About dialog.

### How to use (step list)

- Start `guisaxs-liveview` (from the folder you want to watch, or pick one via **File**).
- Feed TIFFs by drag & drop onto the middle **2D** panel, or by copying/saving files into the watch directory.
- If needed: **Set calibration** → run the wizard → **Run**.
- If needed: **Set buffer** → choose buffer `.dat` and q-range → **Apply**.
- Optional: click a right-column **Analysis** icon to open and arm monodisperse and/or polydisperse processing for new TIFFs.

With neither analysis window open, only integration (+ subtraction if enabled) runs.

### Outputs (watch directory)

All outputs are written under the selected watch directory (layout depends on flat vs tree mode), including:

- per-skill run records under `runs/latest/` (`request.yml`, `result.yml`, `stdout.log`, `stderr.log`)
- `calibration/`, `averaged/` or `averaged_proxy/`, `subtracted/`
- monodisperse: `guinier_mono/<stem>/`, `fit_distances/<stem>/`, `dammif/<stem>/`, `model_bodies/<stem>/`, `denss/<stem>/`, …
- polydisperse: `guinier_poly/<stem>/`, `fit_sizes/<stem>/`, `model_dr_mc/<stem>/`, `mixture/<stem>/`, …
- shared wizard configs (e.g. `fit_distances.conf`, `model_bodies.conf`) next to per-stem folders

---

## `autosaxs` skills reference

This section documents the public *skills* exposed by the `autosaxs` package.

Skills are Python functions in the `autosaxs.skill` package (`repos/src/autosaxs/skill/`) with a fixed signature designed to be callable both from Python and from the `autosaxs` CLI.

### CLI vs Python (how commands are wired)

The `autosaxs` command dispatches subcommands to the corresponding skill functions by introspecting their signatures. In practice:

- Run a skill from the CLI as `autosaxs <command> ...`.
- Every skill supports `-o` / `--output-dir <path>` (maps to the skill's `output_dir` argument, default: `.`).
- Every skill supports caching; use `--cache` to enable it (maps to `use_cache=True` in Python). Use `--no-cache` to explicitly disable it.
- Positional arguments in the CLI match the skill signature order.
- Keyword options use `--kebab-case` names (underscores become `-`).
- Brief CLI `--help` text for skill-specific options comes from the skill docstring section **`### Short parameter list`** (one bullet per parameter: ``- param_name: help text``).

### Path expansion (important API behavior)

Most skills take a **path expression** rather than a strict “single file”:

- A file path is used as-is.
- A directory expands to matching files (non-recursive):
  - 2D inputs: `*.tif`
  - 1D inputs: `*.dat`
- A glob expression is allowed (including `**`); results are sorted, and **empty expansion is an error**.

Note: `autosaxs integrate` accepts either a single path expression **or** multiple image paths on the CLI (the CLI passes a list; the skill normalizes it).

### Caching (opt-in)

- When `use_cache=True`, a skill may write/read a hidden `.cache` YAML file inside its output directory.
- Re-running with the same inputs and relevant options can reuse previously generated output paths if the files still exist and are recent enough (output-integrity check).
- On cache hits, the returned dict includes `from_cache=True` in addition to the usual output path keys.

---

## `calibrate`

SAXS / small-angle x-ray scattering: calibrate detector geometry using a calibration image and a config (ring-analysis + geometry refinement). This is a prerequisite for `integrate` (azimuthal integration).

### Arguments

- `calibrant_image` (str): Path to the calibration image (e.g. TIFF) used for ring analysis.
- `output_dir` (str, default `.`): Directory where results are written.
- `config_path` (str | None, default `None`): Optional path to a YAML config file with a `calibrate` section. When omitted, bundled defaults from the installed `autosaxs` package are used.
- `mask` (str): Path to a mask used during ring analysis. Supports .txt (NuPy format), .msk (Fit2d)
- `mask_mode` (str | None, default `None`): Mask mode selector (`f`/`from_file`, `a`/`auto`, `c`/`combined`). Defaults come from config when omitted.
- `calibrant` (str | None, default `None`): Calibrant name (must be in `pyFAI.calibrant.ALL_CALIBRANTS`). Defaults come from config when omitted.
- `wavelength` (float | None, default `None`): X-ray wavelength in **Ångström**. Defaults come from config when omitted.
- `dist_guess` (float | None, default `None`): Optional initial sample–detector distance in **metres** passed to pyFAI before geometry refinement. When omitted, distance is estimated from the innermost calibration ring.
- `use_cache` (bool, default `False`): Enable/disable caching for this skill run.

Important constraints:

- `mask` is always required by the skill and the CLI (the GUI should treat it as a required field).

### Short parameter list

- mask_mode: Default: load mask from file as is
- calibrant: name of the calibrant, default: AgBh
- wavelength: X-ray wavelength in Ångström
- dist_guess: Optional: initial sample-detector distance in metres (algorithm works good even if this is not set)

### Returns

`dict[str, str]` with these output path roles:

- `integrator_dir`: Directory containing the calibrated integrator (used by `integrate`).
- `refined_path`: Path to the refined calibration YAML.
- `calibration_plots_dir`: Directory containing calibration plots.
- `calibration_curve_plot_path`: Path to the calibration q/I curve plot (PNG).
- `calibration_curve_dat_path`: Path to the calibration q/I curve (`.dat`, same format as integrated 1D curves).
- `calibration_mask_path`: Path to the calibration mask visualization (PNG).

### Python usage

```python
from autosaxs.skill import calibrate

out = calibrate(
    calibrant_image="AgBh.tif",
    output_dir="calibration",
    mask="mask.msk",
    mask_mode="f",
    use_cache=False,
)

print(out["integrator_dir"])
print(out["refined_path"])
```

### CLI usage

```bash
autosaxs calibrate AgBh.tif --output-dir calibration --mask mask.msk
autosaxs calibrate AgBh.tif --conf my_config.conf --output-dir calibration
```

---

## `integrate`

SAXS / small-angle x-ray scattering: integrate 2D SAXS images to 1D curves (q, I, sigma) using a calibrated integrator produced by `calibrate` (azimuthal integration; q-space).

### Arguments

- `images` (str): Image path expression. Can be:
  - a single `.tif` file path
  - a directory (expands to `*.tif`, non-recursive)
  - a glob expression
  - a comma-separated list of file paths (e.g. from multi-file drag & drop)
- `integrator_dir` (str): Path to the calibrated integrator directory (from `calibrate`).
- `output_dir` (str, default `.`): Directory where integrated curves are written.
- `npt` (int, default `1000`): Number of points in the output q grid.
- `use_cache` (bool, default `False`): Enable/disable caching for this skill run.
- `validation_png` (bool, default `False`): If `True`, write a PNG next to each integrated curve showing the source image (log-intensity) with integrator-masked pixels highlighted in semi-transparent red.

### Short parameter list

- npt: Number of integrated points, default: 1000
- validation_png: Show validation image

### Returns

`dict[str, str | list[str]]` with:

- `integrated_1d`: List of paths to integrated 1D `.dat` curves (one per input image).
- `validation_png` (only when `validation_png=True`): List of paths to validation PNG(s), one per input image.

### Python usage

```python
from autosaxs.skill import integrate

out = integrate(
    images="/data/sample_*.tif",
    integrator_dir="calibration/integrator",
    output_dir="integration",
    npt=1000,
    use_cache=False,
)

print(out["integrated_1d"])
```

### CLI usage

```bash
autosaxs integrate "/data/sample_01.tif, /data/sample_02.tif" calibration/integrator       --output-dir integration --npt 1000
```

---

## `average`

SAXS / small-angle x-ray scattering: radiation-damage-aware averaging of per-frame 1D curves.

Expands a path expression to sorted per-frame ``.dat`` files, compares each frame to the
lexicographically first reference (CorMap + reduced chi-squared), truncates at the first
rejection, and writes an inverse-variance weighted merge.

### Arguments

- `profiles` (str): 1D path expression (file / directory / glob / comma-list). Directories expand
  to ``*.dat`` (non-recursive). Files are sorted lexicographically.
- `output_dir` (str, default ``./averaged``): Directory for the averaged curve, frame-selection
  CSV, and report fragments.
- `cormap_p_min` (float, default ``0.05``): CorMap p-value threshold for borderline warnings.
- `chi2_max` (float, default ``1.25``): Reject frame (and stop) when reduced chi-squared vs
  reference exceeds this value.
- `chi2_min` (float, default ``0.9``): Warn when reduced chi-squared is below this value.
- `use_cache` (bool, default ``False``): Enable/disable caching for this skill run.

### Short parameter list

- cormap_p_min: internal parameter, recommended not to change, default: 0.05
- chi2_max: internal parameter, recommended not to change, default: 1.25
- chi2_min: internal parameter, recommended not to change, default: 0.9

### Returns

``dict[str, str]`` with:

- `averaged_1d`: Path to the merged ``int_<prefix>.dat`` curve.
- `frame_selection_csv`: Path to per-frame selection diagnostics CSV.

### Python usage

```python
from autosaxs.skill import average

out = average(
    profiles="integrated/exp_*.dat",
    output_dir="./averaged",
    use_cache=False,
)
print(out["averaged_1d"])
```

### CLI usage

```bash
autosaxs average "integrated/exp_*.dat" --output-dir ./averaged
```

---

## `integrate_proxy`

SAXS / small-angle x-ray scattering: integrate 2D TIFF image(s) to a 1D curve **without detector calibration**, using radial averaging in pixel-radius space (quick-look / debugging; not q-calibrated).

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
- `use_cache` (bool, default `False`): Enable/disable caching for this skill run.

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
    use_cache=False,
)

print(out["integrated_1d"])
```

### CLI usage

```bash
autosaxs integrate-proxy raw/sample_01.tif --output-dir integration_proxy --mask mask.msk --npt 1000
```

---

## `subtract`

SAXS / small-angle x-ray scattering: subtract a buffer curve from a sample 1D profile (background subtraction). Scaling uses either `point_match` (default)
or legacy `match_tail`, optionally restricted to a q window (`q_min` / `q_max`).

### Arguments

- `sample_1d` (str): Sample path expression (file/dir/glob). Directories expand to `*.dat` (non-recursive).
- `buffer_1d` (str): Path to the buffer 1D `.dat` curve (must be an existing file).
- `output_dir` (str, default `.`): Directory where subtraction outputs are written.
- `config_path` (str | None, default `None`): Optional path to a YAML config file with a `subtract` section. When omitted, bundled defaults apply for method/forms; q-window keys come from CLI or user file only.
- `method` (str | None, default `None`): `point_match` or `match_tail`. Defaults from bundled config when omitted.
- `q_min` (float): Lower bound of q-range (nm⁻¹). Required; may be overridden by a user config file `subtract` section.
- `q_max` (float): Upper bound of q-range (nm⁻¹); for `point_match` the match uses this as q intersect (upper edge of the window). Required; may be overridden by a user config file `subtract` section.
- `sample_form` / `buffer_form` (str | None): For `point_match` only — each is `linear`, `Porod`, or `Porod-plus-linear`.
- `point_match_factor` (float | None, default `None`): For `point_match`, scale satisfies `point_match_factor * I_sample_fit(q_max) = scale * I_buffer_fit(q_max)`.
- `scaling_factor` (float | None, default `None`): If provided, overrides automatic scaling and uses this factor directly (must be finite and > 0).
- `use_cache` (bool, default `False`): Enable/disable caching for this skill run.

The q window (`q_min`, `q_max`) is always required at the Python API and CLI. A user config file may supply values that override the arguments passed to `subtract()`.

### Short parameter list

- method: internal parameter, changing the default is not recommended, default: point-match
- sample_form: default: Porod+linear
- buffer_form: default: linear
- point_match_factor: internal parameter, changing the default is not recommended, default: 0.995
- q_min: Required, start of matching region
- q_max: Required, end of matching region, matching point
- scaling_factor: Manual scaling factor. When this set, it replaces auto-scale

### Returns

`dict[str, str]` with:

- `subtracted_1d`: Path to the subtracted curve `.dat`.
- `sub_plot_path`: Path to the subtracted curve PNG (log I vs q).
- `diff_plot_path`: Path to a diff plot PNG.
- `diff_log_plot_path`: Path to a diff plot PNG with log(I) vs q.
Subtraction quality (`correct` or `over-subtracted`) is written into the subtracted `.dat` metadata
(``subtract.correctness``) and into per-sample report fragments (individual Markdown and summary YAML).
The individual report embeds the subtracted curve from the `.dat` (not from `sub_plot_path`).

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
    use_cache=False,
)

print(out["subtracted_1d"])
```

### CLI usage

```bash
autosaxs subtract integration/int_sample_01.dat integration/int_buffer.dat       --output-dir subtracted --method point_match --q-min 4.0 --q-max 6.0
```

---

## `plot`

SAXS / small-angle x-ray scattering: generate standard diagnostic plots for a 1D curve (Guinier, Kratky, log-log):

- Guinier plot (log(I) vs q^2)
- Kratky plot (I*q^2 vs q)
- log-log plot (log(I) vs log(q))

Also writes a Guinier `.dat` file (ln(I) vs q²) used downstream.

### Arguments

- `profile` (str): 1D path expression (file/dir/glob). Directories expand to `*.dat` (non-recursive).
- `output_dir` (str, default `.`): Directory where plot files are written.
- `guinier_q_min` (float | None, default `None`): Lower q bound for selecting Guinier range (enables `guinier_dat_path`).
- `guinier_q_max` (float | None, default `None`): Upper q bound for selecting Guinier range.
- `use_cache` (bool, default `False`): Enable/disable caching for this skill run.

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
    use_cache=False,
)

print(out["guinier_dat_path"])
```

### CLI usage

```bash
autosaxs plot subtracted/sub_sample_01.dat --output-dir plots --guinier-q-min 0.01 --guinier-q-max 0.05
```

---

## `plot_2d`

SAXS / small-angle x-ray scattering: render 2D SAXS TIFF image(s) to PNG using log-intensity scaling (2D detector view).

### Arguments

- `image` (str): 2D path expression (file/dir/glob). Directories expand to `*.tif` (non-recursive).
- `output_dir` (str, default `.`): Directory where PNG(s) are written.
- `use_cache` (bool, default `False`): Enable/disable caching for this skill run.

### Returns

`dict[str, str | list[str]]` with:

- `plot_2d_png`: Path (or list of paths, if `image` is a directory) to generated PNG(s).

### Python usage

```python
from autosaxs.skill import plot_2d

out = plot_2d(
    image="raw/sample_01.tif",
    output_dir="plots_2d",
    use_cache=False,
)

print(out["plot_2d_png"])
```

### CLI usage

```bash
autosaxs plot-2d raw/sample_01.tif --output-dir plots_2d
```

---

## `fit_guinier`

SAXS / small-angle x-ray scattering: fit the Guinier region on a 1D profile (adaptive Rg, I(0), Rg span). Writes:

- a text results file (chosen Guinier parameters and method comparison)
- an ATSAS-format `.dat` file for downstream tools
- a Guinier plot (ln I vs q²) with error bars and the chosen fit line

### Arguments

- `profile` (str): 1D path expression (file/dir/glob). Directories expand to `*.dat` (non-recursive).
- `output_dir` (str, default `.`): Directory where analysis outputs are written.
- `first` (int | None, default `None`): 1-based start point for a fixed-interval Guinier fit (requires `last`).
- `last` (int | None, default `None`): 1-based end point (inclusive) for a fixed-interval Guinier fit (requires `first`).
- `use_cache` (bool, default `False`): Enable/disable caching for this skill run.

### Returns

`dict[str, str]` with:

- `results_path`: Path to the results text file.
- `atsas_dat_path`: Path to the ATSAS-format `.dat` file.
- `guinier_plot_path`: Path to the Guinier fit PNG.

### Python usage

```python
from autosaxs.skill import fit_guinier

out = fit_guinier(
    profile="subtracted/sub_sample_01.dat",
    output_dir="guinier",
    use_cache=False,
)

print(out["results_path"])
```

### CLI usage

```bash
autosaxs fit-guinier subtracted/sub_sample_01.dat --output-dir guinier
```

---

## `analyze_kratky`

SAXS / small-angle x-ray scattering: dimensionless Kratky conformation analysis on a 1D profile.

Builds classical (I·q² vs q) and dimensionless ((q·Rg)²·I/I(0) vs q·Rg) Kratky plots,
locates the global peak, and assigns a model-free conformation class (globular / elongated /
coil / intermediate).

Unless both ``rg_nm`` and ``i0`` are supplied, runs in-process Guinier analysis to obtain them.

### Arguments

- `profile` (str): 1D path expression (file/dir/glob). Directories expand to `*.dat` (non-recursive).
- `output_dir` (str, default `.`): Directory where analysis outputs are written.
- `config_path` (str | None, default `None`): Optional YAML config path for CLI parity; unused by this skill.
- `rg_nm` (float | None, default `None`): Radius of gyration in nm. If omitted, taken from in-process Guinier.
- `i0` (float | None, default `None`): Forward scattering I(0). If omitted, taken from in-process Guinier.
- `q_min`, `q_max` (float | None): Optional q-range (nm⁻¹) applied before analysis.
- `globular_x_min`, `globular_x_max`, `globular_y_min`, `globular_y_max`: Globular peak bands (defaults from quality guide).
- `elongated_x_min`, `elongated_x_max`, `elongated_y_min`: Elongated peak bands.
- `coil_plateau_y`, `coil_plateau_tol`, `coil_high_x_min`: Coil / Debye-plateau detection.
- `x_search_min`, `x_search_max`: Peak search window in q·Rg.
- `use_cache` (bool, default `False`): Enable/disable caching for this skill run.

### Returns

`dict` with:

- `results_path`: Path to the text results file.
- `kratky_plot_path`: Path to the classical Kratky PNG (I·q² vs q).
- `kratky_dimensionless_plot_path`: Path to the dimensionless Kratky PNG.
- `kratky_classical_dat_path`: Path to classical Kratky `.dat`.
- `kratky_dimensionless_dat_path`: Path to dimensionless Kratky `.dat`.
- `classification`: Assigned conformation label.
- `x_max`, `y_max`: Dimensionless peak coordinates (q·Rg, Y).

### Python usage

```python
from autosaxs.skill import analyze_kratky

out = analyze_kratky(
    profile="subtracted/sub_sample_01.dat",
    output_dir="kratky",
    use_cache=False,
)

print(out["classification"])
```

### CLI usage

```bash
autosaxs analyze-kratky subtracted/sub_sample_01.dat --output-dir kratky
autosaxs analyze-kratky subtracted/sub_sample_01.dat --rg-nm 3.2 --i0 1.05 --output-dir kratky
```

---

## `fit_distances`

SAXS / small-angle x-ray scattering: run ATSAS DATGNOM to obtain a pair distance distribution function \(p(r)\) for a monodisperse system from a 1D SAXS curve (real-space distance distribution).

### Arguments

- `profile` (str): 1D path expression (file/dir/glob). Directories expand to `*.dat` (non-recursive).
- `output_dir` (str, default `.`): Directory where the GNOM outputs are written (one subdirectory per input profile).
- `rg_nm` (float | None, default `None`): Expected Rg in nm. If omitted, run in-process Guinier analysis (`fit_guinier`) for an Rg span, then 1D optimize Rg in `[0, 1.5 × rg_max]` (30 s max) scoring each DATGNOM trial as Total Estimate − neg_frac.
- `first` (int | None, default `None`): DATGNOM `--first` (1-based point index). If omitted, taken from the low-q end of the Guinier interval from `fit_guinier`.
- `last` (int | None, default `None`): DATGNOM `--last`. If omitted, `--last` is not passed to DATGNOM.
- `smooth` (float | None, default `None`): DATGNOM `--smooth`. If omitted, defaults to `2.0`.
- `use_cache` (bool, default `False`): Enable/disable caching for this skill run.

### Returns

`dict[str, str | list[str]]` with:

- `output_subdir`: The per-sample output directory used for this profile.
- `gnom_out_paths`: List of DATGNOM `.out` paths written for this profile (typically a single “best” `.out`).
- `best_gnom_out_path`: Path to the selected “best” DATGNOM `.out`.
- `fit_distances_log_path`: Path to the extended run log YAML (`{base}_fit_distances_log.yml`) — candidates, ensemble rows, quality, failures.
- `fit_params_path`: Path to a YAML file containing the fit parameters used for the final run.
- `best_symlink_out_path`: Best-effort symlink path to the selected `.out` (may be missing on some filesystems).
- `fits_csv_path`: Path to a CSV containing candidate scores/metadata.
- `fit_vs_exp_png_path` / `fit_vs_exp_png_error`: Fit-vs-experiment plot output or error message.
- `best_pr_png_path` / `best_pr_png_error`: \(p(r)\) plot output or error message.
- `ensemble_dir` / `ensemble_summary_path`: Close-fits Dmax ensemble directory and CSV summary.
- `close_fit_out_paths`: Saved GNOM `.out` paths for Dmax±10% close fits.
- `force_zero_off_out_path`: Saved GNOM `.out` with `--force-zero-rmax=N` at Dmax.
- `dmax_nm`: Maximum real-space size D_max (nm) from the selected GNOM/DATGNOM fit.
- `rg_pr_nm` / `i0_pr`: Integral Rg and I(0) from p(r) (GNOM-reported or computed from the distribution).
- `rg_guinier_nm`: Guinier Rg (nm) from in-process `fit_guinier` or user `rg_nm`.
- `q_min_fit_nm`: Low-q bound (nm⁻¹) used in the GNOM fit (from the `.out` angular range when available).
- `total_estimate`: GNOM Total Estimate of the selected fit.
- `delta_rg_pct`: \|Rg_Guinier − Rg_P(r)\| / Rg_Guinier × 100.
- `shannon_s_min`, `shannon_class`, `shannon_ok`, `shannon_tip`: Shannon sampling metrics and interpretation guide.
- `pr_quality_class`: `high_quality` \| `acceptable` \| `failed`.
- `overall_status`: `HIGH QUALITY` \| `ACCEPTABLE` \| `FAILED` (quality passport label).
- `quality_rationale` / `user_tips`: Lists explaining the quality assessment.
- `quality_passport_path`: YAML path with the full quality block.

### Python usage

```python
from autosaxs.skill import fit_distances

out = fit_distances(
    profile="subtracted/sub_sample_01.dat",
    output_dir="distances",
    use_cache=False,
)

print(out["best_gnom_out_path"])
```

### CLI usage

```bash
autosaxs fit_distances subtracted/sub_sample_01.dat --output-dir distances
```

---

## `fit_sizes`

SAXS / small-angle x-ray scattering: run ATSAS GNOM (system=1/5) to obtain a size distribution function \(D(R)\) for a polydisperse system from a 1D SAXS curve (polydispersity; spheres/rods).

### Arguments

- `profile` (str): 1D path expression (file/dir/glob). Directories expand to `*.dat` (non-recursive).
- `output_dir` (str, default `.`): Output directory (one subdirectory per input profile).
- `shape` (str, default `spheres`): Polydisperse system model. Options:
    - `spheres`: GNOM `--system=1` (volume distribution for solid spheres).
    - `rods`: GNOM `--system=5` (length distribution for long cylinders). Requires `rad56_nm` (cylinder radius).
    - `ellipsoids`: accepted for API compatibility but **not supported by GNOM command-line** (GNOM system 2 is
      interactive-only). The skill will raise a clear error if selected.
- `rg_nm` (float | None): Optional metadata only (not passed to GNOM); recorded in outputs if set.
- `rmin_nm` (float | None): GNOM `--rmin` (nm). If omitted, not passed to GNOM.
- `rmax_nm` (float | None): GNOM `--rmax` (nm). If omitted, optimized in `[ε, 3 × rg_max]` from in-process `fit_guinier` (30 s max), scoring each trial as Total Estimate − neg_frac.
- `rad56_nm` (float | None): GNOM `--rad56` for `shape=rods` (nm cylinder radius). Ignored for spheres.
- `first` (int | None): GNOM `--first` (1-based). If omitted, taken from the low-q end of the Guinier interval from `fit_guinier`.
- `last` (int | None): GNOM `--last`. If omitted, not passed to GNOM.
- `alpha` (float | None): GNOM `--alpha`. If omitted, not passed to GNOM.
- `nr` (int | None): GNOM `--nr` (number of real-space points). If omitted, GNOM chooses automatically.
- `use_cache` (bool, default `False`): Enable/disable caching for this skill run.

### Short parameter list

- shape: spheres, currently only spheres supported
- rad56_nm: depricated, has no effect
- alpha: regularization parameter, auto-optimized if not set
- nr: number of fitted points, stick to the default

### Returns

`dict[str, str | list[str]]` with:

- `output_subdir`: The per-sample output directory used for this profile.
- `gnom_out_paths`: List of GNOM `.out` paths written for this profile (typically a single “best” `.out`).
- `best_gnom_out_path`: Path to the selected “best” GNOM `.out`.
- `fit_sizes_path`: Compact handoff YAML (`{base}_fit_sizes.yml`) — best fit, quality, analysis, and `model_mixture` hints.
- `fit_sizes_log_path` / `best_summary_path`: Extended run log YAML (`{base}_fit_sizes_log.yml`) — candidates, ensemble, failures.
- `fit_params_path` / `fit_sizes_hints_path` / `quality_passport_path`: Aliases of `fit_sizes_path` (backward compatibility).
- `best_symlink_out_path`: Best-effort symlink path to the selected `.out` (may be missing on some filesystems).
- `fit_vs_exp_png_path` / `fit_vs_exp_png_error`: Fit-vs-experiment plot output or error message.
- `best_dr_png_path` / `best_dr_png_error`: \(D(R)\) plot output or error message.
- `d_avg_nm` / `d_std_nm` / `pdi`: Mean size, standard deviation, and polydispersity index σ/⟨R⟩ from D(R).
- `dr_peak_positions_nm` / `dr_n_peaks`: Peak positions and count in D(R).
- `modality_class`: `monodisperse` \| `unimodal_polydisperse` \| `multimodal` \| `unknown`.
- `modality_confidence`: `high` \| `low` when parametric and peak-based modality hints disagree.
- `parametric_family` / `parametric_aic` / `n_components_suggested` / `mixture_dist_hint` / `parametric_peaks_nm`: Cheap post-hoc parametric hints on D(R).
- `stability_class`: `stable` \| `marginal` \| `unstable` from close-fit ensemble and force-zero-off probe.
- `ensemble_dir` / `ensemble_summary_path` / `close_fit_out_paths` / `force_zero_off_out_path`: Rmax stability probe artifacts (close-fit ensemble + force-zero-off).
- `rmax_validation`: Pathology block from force-zero-off D(R) tail analysis.
- `rg_guinier_nm`: Guinier Rg (nm) when `fit_guinier` ran in-process.
- `total_estimate`: GNOM Total Estimate of the selected fit.
- `sizes_quality_class`: `high_quality` \| `acceptable` \| `failed`.
- `overall_status`: `HIGH QUALITY` \| `ACCEPTABLE` \| `FAILED`.
- `quality_rationale` / `user_tips`: Lists explaining the quality assessment.

### Python usage

```python
from autosaxs.skill import fit_sizes

out = fit_sizes(
    profile="subtracted/sub_sample_01.dat",
    output_dir="sizes",
    shape="spheres",
    use_cache=False,
)

print(out["best_gnom_out_path"])
```

### CLI usage

```bash
autosaxs fit-sizes subtracted/sub_sample_01.dat --output-dir sizes --shape spheres
```

---

## `model_dr_mc`

SAXS / small-angle x-ray scattering: recover a form-free volume-weighted size distribution
\(D(R)\) with per-bin uncertainties using McSAS3 Monte Carlo fitting (polydisperse spheres).

Fits an ensemble of independent sphere-contribution models to a subtracted 1D curve, then
histograms the recovered radii. Bin heights are volume-weighted; error bars are the sample
standard deviation across independent repetitions. For publication-quality uncertainty on
\(D(R)\), raise ``n_rep`` to 50–100 (default 5 is for interactive / pipeline use).

Prerequisites:

- Python package ``mcsas3`` (installed with autosaxs).
- Sphere form factor only in this skill (McSAS3 internal ``mcsas_sphere``).

### Arguments

- `profile` (str): 1D path expression (file/dir/glob). Directories expand to `*.dat` (non-recursive).
- `output_dir` (str, default `.`): Directory where McSAS outputs are written (one subdirectory per profile).
- `config_path` (str | None, default `None`): Optional YAML/config with a `model_dr_mc` section. When omitted, bundled defaults apply.
- `q_min_nm` / `q_max_nm` (float | None): Optional q bounds (nm^-1) for the fit window.
- `n_rep` (int, default `5`): Independent MC repetitions. Mean \(D(R)\) and per-bin \(\sigma\) come from this ensemble; use 50–100 for publication.
- `n_contrib` (int, default `300`): Number of sphere contributions in each MC model.
- `conv_crit` (float, default `1`): Reduced-\(\chi^2\) convergence target. Raise if experimental \(\sigma_I\) are too optimistic and runs never finish.
- `n_cores` (int, default `0`): Parallel workers for repetitions (`0` = autodetect).
- `nbins` (int, default `100`): Rebin count for input \(I(q)\) before fitting.
- `n_bin` (int, default `50`): Number of bins in the post-fit log-\(R\) volume-weighted histogram.
- `max_iter` (int, default `20000`): Max MC iterations per repetition.
- `sld` / `sld_solvent` (float): Scattering-length densities for absolute scaling (`1e-6 Å^-2`). Relative \(I(q)\) still yields a useful relative \(D(R)\).
- `use_cache` (bool, default `False`): Enable/disable caching for this skill run.

Important constraint:

- If you set `q_max_nm`, you must also set `q_min_nm` (otherwise the skill raises `ValueError`).

### Short parameter list

- q_min_nm: start of fit region
- q_max_nm: end of fit region
- n_rep: number of independent MC repetitions; default: 5 (use 50–100 for publication)
- n_contrib: number of sphere contributions per model; default: 300
- conv_crit: reduced-chi2 convergence target; default: 1
- n_cores: parallel workers for repetitions; default: 0 (autodetect)
- nbins: rebin count for input I(q); default: 100
- n_bin: number of D(R) histogram bins; default: 50
- max_iter: max MC iterations per repetition; default: 20000
- sld: particle scattering-length density (1e-6 Å^-2); default: 33.4
- sld_solvent: solvent scattering-length density (1e-6 Å^-2); default: 0.0

### Returns

`dict` with:

- `output_subdir`: Per-sample output directory.
- `state_path`: McSAS3 HDF5/NeXus state (`.nxs`).
- `dr_csv_path`: CSV of \(R\), \(dR\), \(D\), \(D_\mathrm{std}\).
- `stats_path`: YAML with gof, modes, peaks, resolved limits.
- `handoff_path`: Compact YAML hints for `model_mixture`.
- `fit_png_path` / `dr_png_path`: Fit and D(R) plot paths.
- `n_rep`, `r_min_nm`, `r_max_nm`, `q_min_nm`, `q_max_nm`, `n_components_suggested`.

### Python usage

```python
from autosaxs.skill import model_dr_mc

out = model_dr_mc(
    profile="subtracted/sub_sample_01.dat",
    output_dir="mcsas",
    n_rep=5,
    use_cache=False,
)
print(out["dr_png_path"])
```

### CLI usage

```bash
autosaxs model-dr-mc subtracted/sub_sample_01.dat --output-dir mcsas --n-rep 10
```

---

## `model_mixture`

SAXS / small-angle x-ray scattering: run MIXTURE fits on a 1D subtracted curve, select the best model by BIC, and write a comparison plot, size distribution plot, and results CSV (mixture / multi-population size distributions).

Prerequisites:

- Requires the ATSAS `mixture` executable to be available on `PATH` (this skill shells out to `mixture`).

### Arguments

- `profile` (str): 1D path expression (file/dir/glob). Directories expand to `*.dat` (non-recursive).
- `output_dir` (str, default `.`): Directory where the MIXTURE outputs are written.
- `config_path` (str | None, default `None`): Optional path to a YAML config file with a `model_mixture` section. When omitted, bundled defaults apply.
- `q_min_nm` / `q_max_nm` (float | None): Optional q bounds (nm^-1); set via CLI or user config (not in bundled template).
- `maxit`, `max_nph`: MIXTURE parameters; defaults from bundled `model_mixture` section when omitted.
- `plot_I_q` (bool, default `False`): Write I vs q fit comparison plot (labels show BIC).
- `plot_logI_logq` (bool, default `False`): Write log I vs log q fit comparison plot (labels show BIC_log).
- `plot_logI_q` (bool, default `True`): Write log I vs q fit comparison plot (labels show chi2).
- `r_min` (float | None): MIXTURE minimum radius (nm). If omitted, defaults to `0.1`. Converted to Å internally for ATSAS MIXTURE.
- `r_max` (float | None): MIXTURE maximum radius (nm). If omitted, defaults to `rmax_nm` from in-process `fit_sizes`.
- `poly_min` (float | None): MIXTURE minimum polydispersity (nm). If omitted, defaults to `0.05`.
- `poly_max` (float | None): MIXTURE maximum polydispersity (nm). If omitted, defaults to `0.5 × r_max`.
- `use_cache` (bool, default `False`): Enable/disable caching for this skill run.

Important constraint:

- If you set `q_max_nm`, you must also set `q_min_nm` (otherwise the skill raises `ValueError`).

### Short parameter list

- q_min_nm: start of fit region
- q_max_nm: end of fit region
- maxit: number of optimization iterations, default: 100
- r_min: size lower bound
- r_max: size upper bound
- poly_min: spread lower bound
- poly_max: spread upper bound

### Returns

`dict[str, str]` with:

- `output_subdir`: The subdirectory that contains MIXTURE outputs.
- `comparison_path`: Path to the I vs q comparison plot (empty when `plot_I_q=False`).
- `comparison_loglog_path`: Path to the log I vs log q comparison plot (empty when `plot_logI_logq=False`).
- `comparison_log_path`: Path to the log I vs q comparison plot (empty when `plot_logI_q=False`).
- `distributions_path`: Path to the MIXTURE size distributions plot.
- `results_csv_path`: Path to the MIXTURE results CSV.
- `r_max_nm` / `poly_max_nm`: Resolved MIXTURE radius bounds (nm), including defaults when omitted.
- `r_min_nm` / `poly_min_nm`: Resolved MIXTURE radius/polydispersity floors (nm).

### Python usage

```python
from autosaxs.skill import model_mixture

out = model_mixture(
    profile="subtracted/sub_sample_01.dat",
    output_dir="mixture",
    q_min_nm=0.8,
    q_max_nm=2.5,
    use_cache=False,
)

print(out["results_csv_path"])
```

### CLI usage

```bash
autosaxs model-mixture subtracted/sub_sample_01.dat --output-dir mixture --q-min-nm 0.8 --q-max-nm 2.5
```

---

## `model_bodies`

SAXS / small-angle x-ray scattering: run ATSAS `bodies` shape fitting for multiple candidate shapes on a 1D profile, exporting fit files (FIR, PNG, YAML, CSV) and a comparison figure.

### Arguments

- `profile` (str): 1D path expression (file/dir/glob). Directories expand to `*.dat` (non-recursive).
- `output_dir` (str, default `.`): Directory where `bodies` outputs are written.
- `config_path` (str | None, default `None`): Optional YAML config path for CLI parity; this skill does not read a `model_bodies` section (no bundled defaults).
- `shapes` (list[str] | None, default `None`): Subset of body model names to fit (`BODIES_SHAPES_LIST`). `None` or empty means fit **all** models (single `bodies` invocation). A non-empty list runs `bodies --body=...` per shape.
- `first` (int | None, default `None`): Passed to `bodies` as `--first` (1-based data point index). If omitted, taken from the low-q end of the Guinier interval from in-process `fit_guinier`.
- `last` (int | None, default `None`): Passed to `bodies` as `--last` (1-based data point index). Omitted when `None`.
- `use_cache` (bool, default `False`): Enable/disable caching for this skill run.

### Returns

`dict[str, str]` with:

- `output_subdir`: Directory containing the exported `bodies` fit artifacts.

The directory typically contains multiple per-shape FIT files plus aggregated `bodies_fits.yml` and `bodies_fits.csv` if any shapes successfully fit. Each fitted shape also gets `{shape}_pr.dat` and `{shape}_pr.png` (GNOM-style p(r) from the voxel DAM used for 3D views, via Monte Carlo bead-pair sampling).

### Python usage

```python
from autosaxs.skill import model_bodies

out = model_bodies(
    profile="subtracted/sub_sample_01.dat",
    output_dir="bodies",
    shapes=["cylinder", "ellipsoid"],
    first=10,
    last=120,
    use_cache=False,
)

print(out["output_subdir"])
```

### CLI usage

```bash
autosaxs model-bodies subtracted/sub_sample_01.dat --output-dir bodies --shapes cylinder ellipsoid --first 10 --last 120
```

---

## `model_dam`

SAXS / small-angle x-ray scattering: ab initio bead-model shape reconstruction with ATSAS DAMMIF, optionally followed by DAMAVER ensemble averaging (shape reconstruction / bead model / occupancy map). When no GNOM `.out` is supplied, `fit_distances` is run in-process to obtain one.

With `n_runs=1`, runs a single DAMMIF reconstruction. With `n_runs>1`, runs independent DAMMIF replicas then DAMAVER (NSD alignment, outlier rejection, frequency/occupancy map). The data-fitting final shape is the most probable DAMMIF replica (`best.cif` symlink); the DAMAVER frequency map is the stability product. DAMMIN refinement is not performed.

### Arguments

- `profile` (str): 1D path expression (file/dir/glob). Directories expand to `*.dat` (non-recursive).
- `output_dir` (str, default `.`): Directory where DAMMIF / DAMAVER outputs are written.
- `gnom_path` (str | None, default `None`): Optional path to a GNOM/DATGNOM `.out` file for DAMMIF. If omitted, `fit_distances` is run in-process on `profile` and its `best_gnom_out_path` is used.
- `n_runs` (int, default `1`): Number of independent DAMMIF runs. When `>1`, DAMAVER is run on the particle models.
- `dammif_mode` (str, default `fast`): DAMMIF annealing mode: `fast` or `slow`.
- `visualize_all` (bool, default `False`): When True, write PNGs/GIFs under `{output}/visuals/` (synced per-run rotation GIFs, overlap, occupancy threshold; nm scale bar; no run/title captions).
- `use_cache` (bool, default `False`): Enable/disable caching for this skill run.

### Short parameter list

- n_runs: 1 for fast pilot view; 5, 10 or 20 - for reliable averaged shape
- dammif_mode: FAST or SLOW, default FAST; recommended not to change the default
- visualize_all: Heavy visualization with nice GIF's. Not fast, rather production level artifacts

### Returns

`dict[str, str | list[str]]` with:

- `output_subdir`: Directory containing DAMMIF fit artifacts (FIR/CIF and summary files). Each replica also gets `{rep}_pr.dat` and `{rep}_pr.png` (GNOM-style p(r) from DAM bead pairs via Monte Carlo).
- `best_cif_path`: Symlink `best.cif` pointing at the most probable particle CIF (the sole run when `n_runs=1`).
- `best_view_path`: Path to ``best_view.png`` (isosurface + fit overlay for the best model); empty if unavailable.
- `frequency_map_path`: Path to the DAMAVER frequency/occupancy map CIF (empty string when `n_runs=1`).
- `visuals_dir`, `overlap_png`, `overlap_gif`, `occupancy_png`, `occupancy_gif`, `occupancy_thresholds_png`, `run_gifs` when `visualize_all=True` (empty strings / empty list otherwise).

### Python usage

```python
from autosaxs.skill import model_dam

out = model_dam(
    profile="subtracted/sub_sample_01.dat",
    output_dir="dammif",
    gnom_path="guinier/sample_01_gnom.out",
    n_runs=1,
    dammif_mode="fast",
    visualize_all=False,
    use_cache=False,
)

print(out["output_subdir"], out["best_cif_path"])
```

### CLI usage

```bash
autosaxs model-dam subtracted/sub_sample_01.dat --output-dir dammif --n-runs 1 --dammif-mode fast
autosaxs model-dam subtracted/sub_sample_01.dat --output-dir dammif --n-runs 5 --visualize-all
```

---

## `model_density`

SAXS / small-angle x-ray scattering: ab initio continuous electron-density reconstruction with DENSS (Grant protocol; density map / FSC resolution / voxel σ map). Requires the DENSS package (`denss`, `denss-all`, `denss-refine`) installed in the active Python environment.

Protocol `mode`: `pilot` runs a single DENSS reconstruction; `average` runs denss-all (N maps, enantiomer selection, alignment, averaging, FSC) and writes a voxel-wise σ map from the aligned replicas; `refined` runs denss-all then denss-refine of the average against the data (σ still from the denss-all aligned stack). Pipeline q is converted to Å⁻¹ for DENSS staging (never pass autosaxs nm GNOM `.out` files to DENSS unchanged). Alignment is denss-all's built-in procedure (no separate aligner).

### Arguments

- `profile` (str): 1D path expression (file/dir/glob). Directories expand to `*.dat` (non-recursive).
- `output_dir` (str, default `.`): Directory where DENSS outputs are written.
- `gnom_path` (str | None, default `None`): Optional GNOM/DATGNOM `.out` used only for \(D_{\max}\) (nm→Å). Smooth \(I(q)\) comes from the staged Å `.dat` (DENSS may fit internally).
- `mode` (str, default `pilot`): Protocol stage: `pilot`, `average`, or `refined`.
- `denss_mode` (str, default `slow`): DENSS algorithm mode: `slow`, `fast`, or `membrane`.
- `n_maps` (int, default `20`): Number of reconstructions for `average`/`refined` (ignored in `pilot`; must be ≥2 when used).
- `n_jobs` (int, default `1`): Parallel cores for denss-all.
- `visualize_all` (bool, default `True`): When True, write slice GIF/PNG and rotating density/σ GIFs under `{output}/visuals/`.
- `use_cache` (bool, default `False`): Enable/disable caching for this skill run.

### Short parameter list

- mode: Run mode: pilot - quick map view; average - average map across mutliple runs; refined - refined from averaged; default - pilot
- denss_mode: Internal parameter, recommended not to change, default: slow
- n_maps: Number of independent run for average; default 20
- visualize_all: Run visualizations, default: true

### Returns

`dict[str, str]` with:

- `output_subdir`: Directory containing DENSS artifacts for this sample.
- `density_map_path`: Primary density MRC (pilot map, average map, or refined map).
- `avg_map_path`: Averaged MRC path when averaging ran; empty string for `pilot`.
- `sigma_map_path`: Voxel-wise density σ MRC from denss-all `*_aligned.mrc` stack when averaging ran; empty string for `pilot`.
- `fsc_path`: FSC curve path when averaging ran; empty string otherwise.
- `map_fit_path`: Calculated vs experimental fit file when present; else empty.
- `denss_log_path`: Main log for the completed mode.
- `visuals_dir`, `slices_gif`, `midplanes_png`, `density_rotate_gif`, `sigma_rotate_gif` when `visualize_all=True` (empty strings otherwise; `sigma_rotate_gif` empty in `pilot`).

### Python usage

```python
from autosaxs.skill import model_density

out = model_density(
    profile="subtracted/sub_sample_01.dat",
    output_dir="denss",
    mode="pilot",
    denss_mode="slow",
    use_cache=False,
)

print(out["density_map_path"])
```

### CLI usage

```bash
autosaxs model-density subtracted/sub_sample_01.dat --output-dir denss --mode pilot --denss-mode slow
```

---

## `process_monodisperse`

SAXS / small-angle x-ray scattering: run the monodisperse single-profile quality pipeline
(Guinier → dimensionless Kratky → DATGNOM p(r) / Shannon–ΔRg passport → optional DAMMIF
when quality gates pass → per-sample PDF report).

This is a **meta-skill**: it only calls existing leaf skills (`fit_guinier`, `analyze_kratky`,
`fit_distances`, `model_dam`, `report_individual`) and wires outputs between them.
It does **not** change leaf interiors. Steps before Guinier (geometry, averaging, buffer
subtraction) and polydisperse sizing are omitted — input must already be a subtracted
(or otherwise ready) 1D profile.

``model_dam`` runs only when `fit_distances` reports ``high_quality`` / ``HIGH QUALITY``
(quality guide: Total Estimate ≥ 0.55 and ΔRg ≤ 10%). Default ``n_runs=5``.

Primary result: the assembled PDF under ``<output_dir>/reports/`` (includes DAMMIF
fragments when generated).

### Arguments

- `profile` (str): 1D path expression (file/dir/glob of `*.dat`). Directories expand non-recursively.
- `output_dir` (str, default `.`): Pipeline root; leaf skills write under subdirectories here.
- `config_path` (str | None, default `None`): Optional YAML config forwarded to leaf skills.
- `first` / `last` (int | None): Optional fixed Guinier interval (1-based); both required together.
  Guinier `first` is forwarded to DATGNOM; Guinier `last` is **not** passed to DATGNOM
  (window too narrow for p(r)).
- `smooth` (float | None, default `None`): Optional DATGNOM `--smooth` for `fit_distances`.
- `n_runs` (int, default `5`): DAMMIF replica count for `model_dam` when the quality gate passes.
- `use_cache` (bool, default `False`): Forwarded to leaf skills.

### Returns

`dict` with:

- `report_pdf_path`: Primary PDF quality passport (when written).
- `assembled_report_md_path`: Merged Markdown report.
- `pipeline_dir`: The `output_dir` used as the pipeline root.
- `basename`: Sample basename used for report assembly.
- `model_dam_ran`: Whether `model_dam` was invoked.
- `model_dam_skip_reason`: Why DAMMIF was skipped (empty when run).
- `fit_guinier`: Return dict from `fit_guinier`.
- `analyze_kratky`: Return dict from `analyze_kratky`.
- `fit_distances`: Return dict from `fit_distances`.
- `model_dam`: Return dict from `model_dam` (empty dict when skipped).
- `report_individual`: Return dict from `report_individual`.

### Python usage

```python
from autosaxs.skill import process_monodisperse

out = process_monodisperse(
    profile="subtracted/sub_sample_01.dat",
    output_dir="mono_out",
)
print(out["report_pdf_path"])
```

### CLI usage

```bash
autosaxs process-monodisperse subtracted/sub_sample_01.dat --output-dir mono_out
```

---

## `report_individual`

SAXS / small-angle x-ray scattering: build a per-sample report from an existing pipeline directory.

Assembles decentralized ``*_report_individual.md`` fragments, writes
``<pipeline>/reports/<basename>_assembled_report.md``, and builds the PDF with **ReportLab**
from that Markdown (headings, text, images, simple tables).

### Arguments

- `directory` (str): Path to the existing pipeline output directory (the place where intermediate results live).
- `basename` (str): Sample identifier used to match intermediate artifacts within `directory`.
- `output_dir` (str, default `.`): Unused for default paths; PDF/MD default to ``<directory>/reports/``.
- `output_path` (str | None, default `None`): Output PDF path; default ``<directory>/reports/<basename>_report.pdf``.
- `output_md_path` (str | None, default `None`): Optional path for merged Markdown.
- `write_pdf` (bool, default `True`): Whether to emit a PDF.
- `use_cache` (bool, default `False`): Present for CLI parity; unused.

### Returns

`dict[str, Any]` with:

- `report_pdf_path`: Path to the generated PDF when ``write_pdf`` is True.
- `assembled_report_md_path`: Path to merged Markdown.
- `fragments_found`: Number of fragment files merged.

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
autosaxs report-individual pipeline_out sample_01 --output-dir reports
```

---

## `report_summary`

SAXS / small-angle x-ray scattering: build a summary report for all samples in a pipeline directory.

Merges decentralized ``*_report_summary.yaml`` files into Markdown under
``<directory>/reports/summary_assembled_report.md`` and renders the PDF with **ReportLab**
from that Markdown.

### Arguments

- `directory` (str): Path to the existing pipeline output directory.
- `output_dir` (str, default `.`): Unused for default paths; outputs go under ``<directory>/reports/``.
- `output_path` (str | None, default `None`): Output PDF path; default ``<directory>/reports/summary_report.pdf``.
- `output_md_path` (str | None, default `None`): Output path for merged summary Markdown.
- `write_pdf` (bool, default `True`): Whether to emit a PDF.
- `use_cache` (bool, default `False`): Present for CLI parity; unused.

### Returns

`dict[str, Any]` with:

- `report_pdf_path`: Path to the generated PDF when requested.
- `assembled_summary_md_path`: Merged Markdown path.

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
autosaxs report-summary pipeline_out --output-dir reports
```

---
