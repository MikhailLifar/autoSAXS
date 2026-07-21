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
python -m pip install "autosaxs @ git+https://github.com/MikhailLifar/autoSAXS.git@main"
```

Then check the CLI:

```bash
autosaxs --help
```

#### GUI-enabled (`guisaxs`)

Install with GUI extras (adds `customtkinter` and `tkinterdnd2`):

```bash
python -m pip install "autosaxs[gui] @ git+https://github.com/MikhailLifar/autoSAXS.git@main"
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

