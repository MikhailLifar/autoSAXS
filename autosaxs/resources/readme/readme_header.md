# `autosaxs` package + `guisaxs-skills` app + `guisaxs-liveview` app

This project contains:

- **`autosaxs`**: a Python package for building reproducible SAXS processing pipelines (calibration → integration → subtraction → analysis → reports) usable from **Python** or from a **CLI**.
- **`guisaxs-skills`**: a single-window desktop GUI that acts as a strict interface to `autosaxs` skills (discover skills, generate forms from signatures, run via CLI in an isolated process, inspect artifacts and previews).
- **`guisaxs-liveview`**: a live, queued processing GUI that watches a directory for new `.tif/.tiff`, runs calibration/integration/subtraction, and optionally performs automated analysis with continuously updating plots.

---

## `autosaxs` (package)

### Purpose

`autosaxs` is the **data processing core**: it provides the computation and the public “skills” API used by both:

- **CLI users** (the `autosaxs` command), and
- **GUI users** (the `guisaxs-skills` and `guisaxs-liveview` apps).

Its main goal is to make common SAXS processing steps **scriptable, cacheable, and reproducible**, while staying convenient for interactive use.

### Main design choices

- **One public API for everything (“skills”)**: user-facing operations are implemented as Python functions with stable signatures in the `autosaxs.skill` package (`repos/autosaxs/skill/`). The CLI dispatches subcommands to those functions by introspecting signatures and docstrings.
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
python -m pip install "autosaxs @ git+http://hpc.nano.sfedu.ru:8080/mikhail/saxsprocessing.git@main"
```

Then check the CLI:

```bash
autosaxs --help
```

Helper subcommands ship files from the package into a directory of your choice:

- `autosaxs get-readme` — write the generated skills README (`README.md`) into `--output-dir`
- `autosaxs get-skills` — write Cursor Agent Skills (`skills/`) from skill docstrings into `--output-dir`
- `autosaxs get-default-config` — copy the bundled default `config_base.conf` into `--output-dir`

Example:

```bash
autosaxs get-default-config -o .
```

#### GUI-enabled apps (`guisaxs-skills`, `guisaxs-liveview`)

Install with GUI extras (adds `customtkinter` and `tkinterdnd2`):

```bash
python -m pip install "autosaxs[gui] @ git+http://hpc.nano.sfedu.ru:8080/mikhail/saxsprocessing.git@main"
```

This also installs the `guisaxs-skills` and `guisaxs-liveview` console entry points (see below).

---

## `guisaxs-skills` (app)

### Purpose

`guisaxs-skills` is a single-window desktop GUI that acts as a **strict interface to `autosaxs` skills**:

- discovers skills from the public `autosaxs.skill` API,
- builds a parameter form from each skill’s signature,
- runs skills via the `autosaxs` CLI in an **isolated process**,
- shows produced artifact paths and previews (images + common scientific formats).

### Launch

After installing with the GUI extra, start the application:

```bash
guisaxs-skills
```

On startup you will be prompted to choose a **working directory**. The directory may be non-empty; the app will warn about potential overwrites and about cache behavior.

If GUI dependencies are missing, `guisaxs` exits with:

```text
GUI dependencies are not installed. Install with: pip install "autosaxs[gui]"
```

### Layout

The main window is split into three columns:

- **Left: Skill catalog**
  - Lists available skills discovered from `autosaxs.skill`.
  - Selecting a skill opens its form.

- **Middle: Skill form + logs**
  - `Inputs` and `Options` groups generated from the skill signature.
  - Run / Cancel controls and live stdout/stderr streaming.

- **Right: Artifacts + preview**
  - Parsed `key=value` artifact list from the CLI output.
  - Preview panel for images and rendered previews for `.dat`, `.tif/.tiff`, `.csv`, etc.

### How to use (tested scenarios)

The GUI has automated “headless” tests that drive the real UI (no pixel assertions). Typical workflows:

#### Scenario: select a skill → fill inputs/options → run → inspect artifacts

- Select a skill in the catalog (e.g. `calibrate`, `integrate`, `subtract`, `fit_distances`, …)
- Provide required positional inputs in `Inputs` (supports drag & drop / browse / manual path expressions)
- Adjust `Options` (notably `output_dir` and `use_cache`; caching is opt-in)
- Click **Run** and follow logs
- Inspect produced paths in the right-side `Artifacts` list and click for previews

### Outputs (working directory)

`guisaxs-skills` writes its artifacts into the chosen working directory, including:

- `runs/latest/request.yml`, `stdout.log`, `stderr.log`, `result.yml` (traceability for the latest run)
- skill outputs under the configured `output_dir` (typically recommended subfolders like `calibration/`, `averaged/`, `subtracted/`, …)

---

## `guisaxs-liveview` (app)

### Purpose

`guisaxs-liveview` is a single-window desktop GUI for **live, queued processing** of incoming SAXS detector images:

- watches **one** directory for new `.tif/.tiff` files (and also supports drag & drop),
- maintains a processing **queue** and handles files sequentially,
- after calibration (and optional buffer subtraction) continuously updates plots,
- can run optional automatic modeling / analysis from a right-side **Analysis mode** selector.

### Launch

After installing with the GUI extra, start the application:

```bash
guisaxs-liveview
```

On startup you will be prompted to choose a **watch directory** (the app remembers the last valid one).

### Layout

The main window is split into three columns:

- **Left: setup wizards**
  - calibration wizard (“Set calibration”)
  - buffer wizard (“Set buffer”) for subtraction configuration

- **Middle: live view**
  - queue status and latest 2D image
  - navigation controls for processed files

- **Right: Analysis**
  - analysis mode drop-down (Off / p(r) / DAM / primitives / d(r) / mixture)
  - mode-specific controls and previews (fit curves + optional 3D previews)

### How to use (step list)

- Start `guisaxs-liveview` and select the watch directory
- Feed TIFFs by either:
  - drag & drop onto the middle panel, or
  - copying/saving new files into the watch directory
- If needed, set calibration via the left wizard, then (optionally) set buffer subtraction
- Choose an Analysis mode (or keep Off) to enable automatic downstream steps for each new image

### Outputs (watch directory)

All outputs are written under the selected watch directory, including:

- per-skill run records under `runs/latest/` (`request.yml`, `result.yml`, `stdout.log`, `stderr.log`)
- `calibration/`, `averaged/` or `averaged_proxy/`, `subtracted/`
- analysis outputs under per-skill folders like `fit_distances/<stem>/`, `fit_bodies/<stem>/`, `dammif/<stem>/`, `fit_sizes/<stem>/`, `mixture/<stem>/`

---

## `autosaxs` skills reference

This section documents the public *skills* exposed by the `autosaxs` package.

Skills are Python functions in the `autosaxs.skill` package (`repos/autosaxs/skill/`) with a fixed signature designed to be callable both from Python and from the `autosaxs` CLI.

### CLI vs Python (how commands are wired)

The `autosaxs` command dispatches subcommands to the corresponding skill functions by introspecting their signatures. In practice:

- Run a skill from the CLI as `autosaxs <command> ...`.
- Every skill supports `--output-dir <path>` (maps to the skill's `output_dir` argument, default: `.`).
- Every skill supports caching; use `--cache` to enable it (maps to `use_cache=True` in Python). Use `--no-cache` to explicitly disable it.
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

### Caching (opt-in)

- When `use_cache=True`, a skill may write/read a hidden `.cache` YAML file inside its output directory.
- Re-running with the same inputs and relevant options can reuse previously generated output paths if the files still exist and are recent enough (output-integrity check).
- On cache hits, the returned dict includes `from_cache=True` in addition to the usual output path keys.

