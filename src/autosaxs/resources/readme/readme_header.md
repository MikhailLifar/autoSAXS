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

