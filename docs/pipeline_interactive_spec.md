# Technical specification: `Controller.pipeline_interactive`

Specification of `Controller.pipeline_interactive` in the **autosaxs** package (`src/autosaxs/`): single source of truth for implementation and refactoring. **autosaxs** is a pip-installable Python package: it can be installed via `pip install -e .` from `autosaxs/` (package repo) (when `pyproject.toml` is present) or from a built wheel. Pipeline invocation lives in **`saxsprocessing/pipeline.py`**; run via `python pipeline.py` (or `python -m pipeline`) from `autosaxs/` (package repo).

---

## 1. Purpose and scope

**Purpose:** Interactive SAXS pipeline: detector calibration; 2D→1D integration; optional buffer subtraction; descriptors (Rg, I(0), P(R) via ATSAS); Guinier/Kratky/log-log plots; optional polydispersity and shape fitting (MIXTURE/ATSAS, BODIES, DAMMIF, polydisperse spheres); optional AI analysis; per-profile PDF reports for all sample profiles; and a single summary PDF report combining all sample curves. File-based I/O with directory and file upload prompts.

---

## 2. Technological stack

- **Technologies:** Python 3; YAML for config and metadata; CSV for tabular data in 1D curve files; JSON for integrator params and GUI protocol.
- **External packages (as used in this pipeline):**
  - **ATSAS** — Software package for SAXS processing. It is responsible for descriptors calculation, fitting with geometric primitives (BODIES component of ATSAS), "ab initio" fitting with Dummy Atom Model (DAMMIF component of ATSAS), and mixture fitting (MIXTURE component: polydisperse spheres).
  - **customtkinter** — Used by `autosaxs.gui_interface` (and `autosaxs.gui`) for pipeline/step selection, profile selection, and short-lived dialogs per query.
  - **matplotlib** — Plotting (viewer, gui).
  - **numpy, pandas** — Arrays and tabular data.
  - **scipy** — e.g. `scipy.special.gamma`, interpolation; **scipy.ndimage** in processor.
  - **pyFAI** — Calibration and integration (`pyFAI`, `pyFAI.calibrant`, `pyFAI.detectors.Pilatus1M`, `pyFAI.geometryRefinement.GeometryRefinement`, `pyFAI.io.image` for reading images).
  - **fabio** — Reading `.msk` mask files.
  - **ase** — Atoms and structure (e.g. `ase.io.read`, `ase.Atoms`) for viewer and CIF from DAMMIF.
  - **yaml** — Config and metadata.
  - **polydispfit** — Polydisperse sphere fitting (project-local module).
  - **LLM for AI analysis** — Provided by **autosaxs.foreign.aiAssistantFramework.lib.llm** (vendored copy; no external aiAssistantFramework dependency).
  - **Plot/smoothing utilities** — Provided by **autosaxs.foreign.supervised_ml** (plot_util, util, whittaker_smooth; vendored copy; no external supervised_ml dependency).
  - **reportlab** — PDF generation for per-profile reports (text, tables, embedded figures).
- **Package and installation:** **autosaxs** is intended to be installed as a Python package via **pip**. From the repository root that contains `autosaxs/` (package repo): run `pip install -e .` from `autosaxs/` (package repo) (where `pyproject.toml` defines the package) for an editable install, or build a wheel and install it. The pipeline entry point **`saxsprocessing/pipeline.py`** imports `autosaxs` and runs `controller.pipeline_interactive(...)`; it can be executed from `autosaxs/` (package repo) (e.g. `python pipeline.py`).
- **Project-internal modules and usage:**
  - **autosaxs** (`src/autosaxs/`): Pip-installable package containing the pipeline core. Modules: **processor**, **utils**, **event_bus**, **cli_interface**, **gui_interface**, **api**, **viewer**, **context**, **saxs_controller** (exports `Controller`), **gui**, **polydispfit**, **mixture**, **report**; subpackage **foreign/** (vendored dependencies, see below). Directories: **global/** (env.yml, primus_models.yml, templates/), **prompts/** (e.g. prompts/visual/ for AI/plot prompts), **pipelines/** (pipeline description .txt files, e.g. protein_v0.txt, concentration.txt). Other code (e.g. `pipeline.py`, `guisaxs`, GUI calibration subprocess runner) imports from `autosaxs` as needed.
  - **foreign** (`autosaxs/foreign/`): Vendored copies of external packages, preserving original import structure. **No external supervised_ml or aiAssistantFramework packages are required.** Structure: **foreign/supervised_ml/** (plot_util.py, util.py, whittaker_smooth.py); **foreign/aiAssistantFramework/lib/** (llm.py). Pipeline imports: `from autosaxs.foreign.supervised_ml.whittaker_smooth import whittaker_smooth` (used in utils/processor); `from autosaxs.foreign.supervised_ml.plot_util import *` (used in viewer); `from autosaxs.foreign.aiAssistantFramework.lib import llm` (used in saxs_controller for AI analysis).
  - **processor** (`autosaxs/processor.py`): `IntegratorExtended`, `integrate_2d_to_1d`, `subtract_buffer`, `find_center`, `find_rings`, `refine`, `get_detector`, `get_interring_dist_px` (and `*` import so e.g. mask/geometry helpers used by calibration). Calibration and integration logic live here.
  - **utils** (`autosaxs/utils.py`): `read_from_tiff` (via processor’s use of `utils`); `read_saxs`, `write_saxs`, `write_data`, `read_data`, `load_config`, `save_config`, `get_pipeline_paths`, `get_pipeline_description`, `map_sample_files_to_buffer_files`, `read_bodies_cif`, `calc_chi2`, `calculate_atoms_density_and_isosurface`, `calculate_shape_density_and_isosurface`; `gaussian_pdf`, `schultz_pdf`; `ENV` (from `autosaxs/global/env.yml`); `whittaker_smooth` (from `autosaxs.foreign.supervised_ml.whittaker_smooth`). **Within the autosaxs package, paths (e.g. LATEST_STEPS_PATH, pipeline description files) are derived from the package directory `_autosaxs_dir`** (e.g. `os.path.join(_autosaxs_dir, "temp", "latest_steps.yml")`; pipeline descriptions from `_autosaxs_dir/pipelines/<name>.txt`).
  - **event_bus** (`autosaxs/event_bus.py`): `EventBus` and event type enum. See §3.1 for catalogue and payloads.
  - **cli_interface** (`autosaxs/cli_interface.py`): CLI: stdin/print, file monitoring, `connect(bus)`. Defines `PipelineInterrupt`, `CLIInterface`. Same EventBus contract as GUI and API.
  - **gui_interface** (`autosaxs/gui_interface.py`): GUI: CustomTkinter dialogs per query; uses `autosaxs.gui` for step/profile selection.
  - **gui** (`autosaxs/gui.py`): GUI helpers for pipeline/step and profile selection (e.g. `_run_gui_interactive`, `_run_choose_profiles_gui`); used by `gui_interface`.
  - **polydispfit** (`autosaxs/polydispfit.py`): Polydisperse sphere fitting; used by Controller for the polydispfit step. Uses tabular lookup tables (e.g. `sphere.npz`) under `autosaxs/global/tabular/`; the pipeline regenerates missing .npz data before fitting.
  - **mixture** (`autosaxs/mixture.py`): MIXTURE-based fitting (ATSAS). Single entry point **`fit_mixtures(profile_path, output_dir, ...)`**: runs 6 MIXTURE fits (1-, 2-, 3-phase × Gaussian, Schultz–Zimm; SPHERE-only), parses outputs, computes R², R²_adj, BIC on direct and log-clipped intensities, selects the model with lowest BIC_log, and writes comparison plot (I vs q and log I vs q), distribution plot (R in nm), and results CSV. Input: 1D curve (q in nm⁻¹, intensity, sigma). Output directory under working dir (`mixture/mixture_<basename>`). **Invoked per selected profile in second (slow) processing**.
  - **viewer** (`autosaxs/viewer.py`): Visualization (`view_calibration`, `view_mask`, `view_curves`, `plot_3d_views_and_scattering`, etc.); uses plot helpers from `autosaxs.foreign.supervised_ml.plot_util`. Entry point `pipeline.py` uses `PLTViewer` when wiring the pipeline.
  - **context** (`autosaxs/context.py`): `Context` — holds directory, config, and path groups; config and path accessors.
  - **report** (`autosaxs/report.py`): All per-profile PDF functionality and the summary (all-curves) PDF. Builds a single PDF from a report-data dictionary (e.g. `build_report_pdf(report_data: dict, output_path: str)`). Only sections for which data is present are included. Summary report: one PDF combining all sample curves (integrated, subtracted, descriptors table, Guinier/Kratky/log-log overplots). See §6 Report.
  - **api** (`autosaxs/api.py`): Script-based interface to the pipeline. Encapsulates responding to pipeline requests (directory, files, steps, profile selection, choices) from function arguments and hardcoded values instead of user input. Same EventBus contract as CLI/GUI. See §3.2 Script-based API.
  - **pipeline** (`saxsprocessing/pipeline.py`): Entry point for the interactive pipeline. Wires EventBus; connects one of `autosaxs.cli_interface`, `autosaxs.gui_interface`, or scripted use via `autosaxs.api`; creates `Controller` from `autosaxs.saxs_controller` and `PLTViewer` from `autosaxs.viewer`; runs `controller.pipeline_interactive(...)`. No pipeline logic—only wiring and invocation.

---

## 3. Architecture

- **Type of application:** Interactive pipeline; one interface (CLI, GUI, or API) connected to EventBus. File-based working directory; files from user or from API responses.
- **Module layout:** **autosaxs** (`src/autosaxs/`) — pip-installable: `event_bus`, `cli_interface`, `gui_interface`, `api`, `processor`, `utils`, `viewer`, `context`, `saxs_controller`, `gui`, `polydispfit`, `mixture`, `report`; **foreign/** (vendored: `foreign/supervised_ml/`, `foreign/aiAssistantFramework/lib/`); `global/`, `prompts/`, `pipelines/`. **`pipeline.py`** in `autosaxs/` (package repo) (entry point when running from repo). One EventBus; Controller + one interface. Wiring in `pipeline.py` or in script when using API.
- **Layers:**
  - **EventBus** (`autosaxs/event_bus.py`): Single channel for Controller–Interface I/O; no direct calls. Event enum and payloads in `event_bus.py`; semantics in §3.1.
  - **CLI** (`autosaxs/cli_interface.py`): Subscribes via `connect(bus)` to **FILE_REQUESTED**, **CHOICE_REQUESTED**, **DIRECTORY_REQUESTED**, **MESSAGE**, **PIPELINE_STEPS_REQUESTED**, **PROFILE_SELECTION_REQUESTED**; publishes **FILE_UPLOADED**, **FILE_UPLOAD_CANCELED**, **OPTION_CHOSEN**, **OPTION_CHOICE_CANCELED**, **DIRECTORY_SPECIFIED**, **PROGRAM_INTERRUPTED**, **PIPELINE_STEPS_SPECIFIED**, **PROFILE_SELECTION_SPECIFIED**. stdin/print + file monitoring; pipeline/step and profile selection in-module. Also defines `CLIInterface` and `PipelineInterrupt` for standalone use.
  - **GUI** (`autosaxs/gui_interface.py`): Same events. Short-lived dialogs (directory, file, choice, message); pipeline/step and profile selection as dialogs (uses `autosaxs.gui`).
  - **API** (`autosaxs/api.py`): Script-driven interface; responds from function arguments and hardcoded values. See §3.2.
  - **Controller** (`autosaxs/saxs_controller.py`): Publishes requests to EventBus, subscribes to responses; calls processor, context, viewer, step logic.
  - **processor**, **viewer**, **context**, **utils**: Calibration/integration, plotting, config/paths, I/O.
- **How the pipeline is invoked and wires components:** Entry point `pipeline.py` (in saxsprocessing) or script calling `autosaxs.api`:
  1. Create EventBus; Controller and one of `autosaxs.cli_interface` / `autosaxs.gui_interface` / API (via `autosaxs.api`) connect to it. Controller creates `Context()` when it runs the pipeline.
  2. Steps via EventBus (pipeline/step selection); then **DIRECTORY_REQUESTED** → **DIRECTORY_SPECIFIED** or **PROGRAM_INTERRUPTED**; then **FILE_REQUESTED** for `config.conf` → **FILE_UPLOADED** or cancel. Load config.
  3. If `calibration` in steps: **FILE_REQUESTED** (calibrant) → response; **CHOICE_REQUESTED** (mask mode) → **OPTION_CHOSEN** / cancel; if mask from file/combined, **FILE_REQUESTED** (mask) → response. Run `autocalib(...)` or load integrator from disk when `integration` without calibration.
  4. Main loop: **FILE_REQUESTED** (buffer/sample 2D or 1D, or `subtracted/*.dat`) → **FILE_UPLOADED** or cancel; on alignment errors **MESSAGE** then retry. Run integration/subtraction as per steps. If `simple_analysis` is in steps, run **simple_analysis for all sample profiles** (before any profile choice). If `plots` is in steps, run **plots for all sample profiles** (before any profile choice). Failures in simple_analysis, plots (and in mixture, polydispfit, bodies, dammif, ai_analysis below) are caught and reported via **MESSAGE**. Then build and save reports for **all sample profiles** (first pass; report data from integration through subtraction; descriptors if simple_analysis was run; plot figures if plots was run; see §6 Report). If there is at least one step **after** `simple_analysis` and **after** `plots` (i.e. one of `mixture`, `polydispfit`, `bodies`, `dammif`, `ai_analysis`): profile selection via EventBus → `selected_profiles`; for each **selected** profile run those remaining steps (each of mixture, polydispfit, bodies, dammif, ai_analysis in try-except; report failures via **MESSAGE**) and build/save report again (second pass; full data; same output path). If there are **no** steps after `simple_analysis`/`plots` (processing stops at simple_analysis or plots or earlier), do **not** request profile selection; proceed directly to the “Upload more data?” step.
  5. Return `context`. On **PROGRAM_INTERRUPTED**, exit (e.g. raise `PipelineInterrupt`).

### 3.1 EventBus: events and data flow

- **Channel:** Only path for Controller–Interface I/O. Enum and payloads in `autosaxs/event_bus.py`. One response per request  “Interface” = connected CLI, GUI, or API.
- **Events and payloads:**
  - **DIRECTORY_REQUESTED** — Controller. Payload: `query` (str). → **DIRECTORY_SPECIFIED** or **PROGRAM_INTERRUPTED**.
  - **DIRECTORY_SPECIFIED** — Interface. `path` (str).
  - **FILE_REQUESTED** — Controller. `directory`, `query`, `filepattern`, `obligatory`, `skip_if_exists`, `except_prev_paths`, `allow_same_time`, … → **FILE_UPLOADED** or **FILE_UPLOAD_CANCELED** (or **PROGRAM_INTERRUPTED** if obligatory).
  - **FILE_UPLOADED** — Interface. `paths` (list of str).
  - **FILE_UPLOAD_CANCELED** — Interface. optional `reason`. If obligatory, may send **PROGRAM_INTERRUPTED** instead/additionally.
  - **CHOICE_REQUESTED** — Controller. `query`, `options` (key→label), `default_op`. → **OPTION_CHOSEN** or **OPTION_CHOICE_CANCELED** or **PROGRAM_INTERRUPTED**.
  - **OPTION_CHOSEN** — Interface. `choice` (str, key from `options`).
  - **OPTION_CHOICE_CANCELED** — Interface. optional payload; may add **PROGRAM_INTERRUPTED** if required.
  - **MESSAGE** — Controller. `text` (str). Interface displays; no response.
  - **PROGRAM_INTERRUPTED** — Interface. optional `reason`. Controller must exit pipeline.

### 3.2 Script-based API (`autosaxs.api`)

**api** (`autosaxs/api.py`): script-driven interface; responds from function arguments and hardcoded values. Each function is specified by signature and by how it answers each pipeline request (directory, files, steps, choices, profile selection; MESSAGE handling for failures).

#### 3.2.1 `fast_first_processing(directory, steps=None, mask_choice=None)`

- **Signature:** `fast_first_processing(directory, steps=None, mask_choice=None)` — `directory` is the working directory path (str). `steps`: optional list of step names (e.g. `['calibration', 'integration']`); default `None` → `['calibration', 'integration', 'subtraction', 'simple_analysis', 'plots']`. `mask_choice`: optional; when calibration asks for mask mode, use this choice: `'a'` (automask), `'f'` (from file), `'c'` (combine); default `None` → `'c'`.
- **Steps:** Configurable via `steps`; default `['calibration', 'integration', 'subtraction', 'simple_analysis', 'plots']`.
- **Responses to pipeline requests:**
  - **PIPELINE_STEPS_REQUESTED** → **PIPELINE_STEPS_SPECIFIED** with `steps` (the function argument or default).
  - **DIRECTORY_REQUESTED** → **DIRECTORY_SPECIFIED** with `path` = `directory` (the function argument).
  - **FILE_REQUESTED** for `config.conf` → resolve path under `directory`; if file exists, **FILE_UPLOADED** with that path; otherwise raise an error (file is required to exist).
  - **FILE_REQUESTED** for `raw/*_calib.tif` → expect file(s) to exist under `directory`; if requested (e.g. not found / skip_if_exists did not apply), raise an error; otherwise **FILE_UPLOADED** with the existing path(s).
  - **CHOICE_REQUESTED** (mask mode) → **OPTION_CHOSEN** with `choice` = `mask_choice` (the function argument or default `"c"`).
  - **FILE_REQUESTED** for `mask*` → only when mask mode is from_file or combined (i.e. `mask_choice` is `'f'` or `'c'`). Expect file to exist under `directory`; if requested and not found, raise an error; otherwise **FILE_UPLOADED** with the existing path(s).
  - **FILE_REQUESTED** for `raw/*_buffer.tif` → same as `raw/*_calib.tif`: expect to exist; if requested and not found, raise an error; otherwise **FILE_UPLOADED** with existing path(s).
  - **FILE_REQUESTED** for `raw/*_sample.tif` → same as above: expect to exist; if requested and not found, raise an error; otherwise **FILE_UPLOADED** with existing path(s).
  - **MESSAGE** with alignment-failure text (overlapped / not_paired buffer–sample) → raise an error (do not let the pipeline retry).
  - **PROFILE_SELECTION_REQUESTED** → not used in this function’s flow; if ever reached, raise.
  - “Upload more data?” / continuation prompt → **OPTION_CHOSEN** so that the pipeline stops after one cycle (no further data upload); just answer "no".

All “expect to exist, else raise” behavior means: when the Controller requests the file (FILE_REQUESTED), the API resolves the path(s) under `directory`; if the file(s) do not exist, the API raises an error instead of publishing **FILE_UPLOAD_CANCELED** or **FILE_UPLOADED**.

#### 3.2.2 `slow_second_processing(directory, selected_profiles)`

- **Signature:** `slow_second_processing(directory, selected_profiles)` — `directory` is the working directory path (str); `selected_profiles` is a list of file names (e.g. basenames from the subtracted subdirectory, such as `["sub_foo.dat", "sub_bar.dat"]`). The implementation filters the controller’s `profiles_data` by these names and publishes the resulting dict (basename → profile) in **PROFILE_SELECTION_SPECIFIED**.
- **Steps:** `['mixture', 'polydispfit', 'bodies', 'dammif']`. These steps run per selected profile (second, slow processing).
- **Responses to pipeline requests:**
  - **PIPELINE_STEPS_REQUESTED** → **PIPELINE_STEPS_SPECIFIED** with `steps` as above.
  - **DIRECTORY_REQUESTED** → **DIRECTORY_SPECIFIED** with `path` = `directory` (the function argument).
  - **FILE_REQUESTED** (e.g. `config.conf`, or any file in this flow) → resolve under `directory`; if required file does not exist, raise an error; otherwise **FILE_UPLOADED** with the path(s).
  - **PROFILE_SELECTION_REQUESTED** → **PROFILE_SELECTION_SPECIFIED** with `selected_profiles` = dict built from the function argument (filtering the controller’s `profiles_data` by the given list of names).
  - **CHOICE_REQUESTED** / **MESSAGE** / continuation prompts → as needed so the pipeline runs only the second-pass processing for the given steps and selected profiles; exact behavior is to ensure no further data upload, i.e. answer “no” to “Upload more data?”.

The function assumes the pipeline is run in a context where calibration, integration, and subtraction have already been done (e.g. after `fast_first_processing` or equivalent), so that `directory` contains `config.conf`, `subtracted/*.dat`, and any other inputs required for the listed steps.

---

## 4. User workflow (step-by-step)

- **File inputs:**
  - **Config:** Required. Filename requested: `config.conf` (content is YAML). Must be in the chosen working directory.
  - **Calibrant (2D):** Required if step `calibration` is selected. Pattern: `raw/*_calib.tif`.
  - **Mask:** Required only if mask mode is `from_file` or `combined`. Pattern: `mask*`; extensions supported in code: `.msk`, `.npy`, `.txt`.
  - **Buffer 2D:** If steps include `integration` and `subtraction`. Pattern: `raw/*_buffer.tif`.
  - **Sample 2D:** If step `integration` is selected. Pattern: `raw/*_sample.tif`.
  - **Buffer 1D / Sample 1D:** If step `subtraction` is selected but not `integration`. Patterns: `averaged/*_buffer.dat`, `averaged/*_sample.dat`.
  - **Pre-subtracted profiles:** If step `subtraction` is not selected. Pattern: `subtracted/*.dat`.
- **Validation rules (from code):**
  - **Images:** No explicit extension check in controller; `processor` uses `pyFAI.io.image.read_image_data` and `read_from_tiff` (same). File patterns use `*_calib.tif`, `*_buffer.tif`, `*_sample.tif` — effectively `.tif`.
  - **Mask:** Loaded in `IntegratorExtended.read_mask`: `.npy` and `.txt` cast to `bool`; `.msk` read via fabio and cast to bool (and flipped vertically). So values must be interpretable as boolean (0/1 or boolean-like). No other validation in controller.
  - **1D curves:** Must be readable by `read_saxs` (see “Data and file formats”).
- **Order of operations:**
  1. Directory and config: user provides directory; config file `config.conf` is loaded.
  2. If `calibration` in steps: wait for calibrant; if mask mode is from_file or combined, wait for mask; run `autocalib` → writes `integrator_params/`, `calibration.png`, `calibration_mask.png`, and updates config with `refined`.
  3. If `integration` in steps and not `calibration`: wait until directory contains `integrator_params` with `ai_params.json`, `detector_params.json`, `mask.npy`; then load `IntegratorExtended` from disk.
  4. Main cycle:
     - If `integration` in steps: wait for buffer (if `subtraction` in steps) and sample 2D files; align sample↔buffer by name; integrate all buffer and sample images → 1D in `averaged/`; add paths to context.
     - If `subtraction` in steps and `integration` not in steps: wait for buffer and sample 1D in `averaged/`; align; set `buffer_paths_1d` and `sample_paths_1d` (and basename list from sample names).
     - If `subtraction` in steps: align `sample_paths_1d` with `buffer_paths_1d`; for each aligned pair run `subtract` → outputs in `subtracted/`; extend context with buffer_1d and sample_1d paths.
     - If `subtraction` not in steps: wait for `subtracted/*.dat`; for each file load, plot, and build basename list and profile paths.
  5. **simple_analysis and plots for all:** If `simple_analysis` in steps, run for every sample profile (outputs in `descriptors/`). If `plots` in steps, run for every sample profile (outputs in `plots/`). Before first report pass and before profile selection.
  6. **First report pass:** For all sample profiles: collect report data (integration→subtraction; descriptors if simple_analysis was run; plot figures if plots was run); build and save one PDF per profile (`reports/<basename>_report.pdf`). Then build and save the **summary report** once per cycle (`reports/summary_report.pdf` or equivalent), combining all sample curves (see §6). After simple_analysis and plots (when in steps), before profile selection. See §6.
  7. **Profile selection (conditional):** If at least one step after `simple_analysis`/`plots` (mixture, polydispfit, bodies, dammif, ai_analysis): request **PROFILE_SELECTION_REQUESTED**; result `selected_profiles` (dict basename → profile). Otherwise skip to step 9.
  8. **Per-selected steps and second report pass (conditional):** Only if step 7 was done. For each selected profile: run remaining steps (mixture, polydispfit, bodies, dammif, ai_analysis); build and save report again (full data; same path as first pass). See §6.
  9. Ask whether to upload more data (e.g. “Upload more data?” or extended prompt); if answer does not start with “n”, repeat from step 4 (new buffer/sample or profiles); otherwise exit and return context.

- **Processing:** When `integration` in steps, 2D files are integrated in one go after alignment. Subtraction runs in the same cycle after integration (or after loading 1D files); pairing via `map_sample_files_to_buffer_files` (see §11 naming).

---

## 5. Data and file formats

- **2D images:** Read with `pyFAI.io.image.read_image_data` (in `utils.read_from_tiff`). Patterns in the app: `raw/*_calib.tif`, `raw/*_buffer.tif`, `raw/*_sample.tif` — effectively TIFF (`.tif`).
- **Mask:** Extensions: `.npy` (NumPy load, cast to bool), `.txt` (loadtxt, cast to bool), `.msk` (fabio, cast to bool, then flipped on first axis). Values must be 0/1 or boolean-like; no additional validation in code.
- **1D SAXS curves (read_saxs/write_saxs):** Format is the generic “YAML metadata + CSV data” used by `read_data`/`write_data` in `utils.py`: file starts with a YAML block between `---` and `...`, then a line `# Data in CSV format\n`, then CSV. For SAXS, CSV has columns `q`, `intensity`, and optionally `sigma`. Metadata is a dict. Any file path passed to `read_saxs` must conform to this layout.
- **Config:** `config.conf` in working directory; YAML. Keys in §7.
- **Integrator persistence:** `integrator_params/` with `ai_params.json`, `detector_params.json`, optional `mask.*`; used when `integration` without `calibration`.
- **Report:** §6.
- **Bodies/dammif fit exports:** Bodies step produces `bodies_fits.yml` (YAML dict of shape→{params, chi2}) and `bodies_fits.csv` (q, exp, fitted intensities over fit q-range). Dammif step produces `dammif_fits.yml` (YAML dict of replicate→{Rg, Dmax, V, f_ratio, chi2}) and `dammif_fits.csv` (same CSV layout). Layout and semantics in §9.

---

## 6. Report (per-profile PDF and summary PDF)

Reports: one PDF per sample profile in `reports/<basename>_report.pdf`, plus one **summary report** PDF combining all curves in `reports/summary_report.pdf`. Not a selectable step.

### 6.1 Per-profile reports

- **First pass:** After main cycle (integration, subtraction) and, if in steps, after simple_analysis and plots for all. For every sample profile: collect data (integration→subtraction; descriptors if simple_analysis was run; plot figures if plots was run). Build and save one PDF. Content: sections (1)–(3); (4) when simple_analysis was run; (5) when plots was run. Not-selected profiles get no further passes.
- **Second pass:** Only for selected profiles. After remaining steps (mixture, polydispfit, bodies, dammif, ai_analysis): collect full report data; build and save PDF at same path (overwrites first pass). Content: sections (1)–(5), (5b)–(7) as data exists.
- **Report data:** Dict per profile; only present keys rendered. Keys: integrated curve, difference plot (`diff_<basename>.png`), subtracted plot (`sub_<basename>.png`), descriptors table (Rg, I(0), Quality, Dmax (nm), MW from Rg (kDa), MW from DATMW (kDa)), plot figures (Guinier, Kratky, log-log; no I vs q — it duplicates the subtracted curve), **mixture** (best model label, BIC_log, comparison figure, distribution figure, mixture results table/CSV summary), fits comparison figure(s), fits table. First pass: integration→subtraction (+ descriptors if simple_analysis run; + plot figures if plots run).
- **PDF sections (optional):** (1) Integrated curve. (2) Difference plot. (3) Subtracted plot. (4) Descriptors table. (5) Plot figures (Guinier, Kratky, log-log only). (5b) Mixture: best model (lowest BIC_log), comparison plot (I vs q and log I vs q), distribution plot (R in nm), and mixture results table/summary. (6) Fits figure(s): each titled by fit type (e.g. "Fits comparison, polydispfit", "Fits comparison, bodies", "Fits comparison, dammif"). (7) Fits table.
- **Implementation:** `autosaxs.report.build_report_pdf(report_data, output_path)`. No EventBus events.

### 6.2 Summary report (all curves, one PDF)

- **Purpose:** One PDF that aggregates all sample profiles for quick comparison. Output path: `reports/summary_report.pdf` (or equivalent single file per cycle).
- **When built:** Once per main cycle, after the first report pass (after all per-profile reports are built for that cycle). Uses the same data already collected for per-profile reports (integration, subtraction, descriptors if simple_analysis was run, plot data if plots was run).
- **Content (only sections for which data exists):**
  1. **All integrated curves, one axes** — One figure: every sample’s integrated curve on the same axes (q vs intensity); legend or labels by sample basename.
  2. **All subtracted curves, one axes** — One figure: every sample’s subtracted curve on the same axes; legend or labels by sample basename.
  3. **Descriptors table** — Single table: **rows** = samples (one row per sample basename); **columns** = same descriptor set as in per-profile reports (Rg, I(0), Quality, Dmax (nm), MW from Rg (kDa), MW from DATMW (kDa)). Include only descriptors that exist for at least one sample; missing cells left empty or marked as N/A.
  4. **Guinier plots for all samples, one axes** — One figure: Guinier representation (e.g. log I vs q²) for every sample on the same axes; legend or labels by sample basename.
  5. **Kratky plots for all samples, one axes** — One figure: Kratky representation (e.g. q²×I vs q) for every sample on the same axes; legend or labels by sample basename.
  6. **Log-log plots for all samples, one axes** — One figure: log I vs log q for every sample on the same axes; legend or labels by sample basename.
- **Data source:** Integrated and subtracted curves from context/profile data; descriptors from simple_analysis results (e.g. `descriptors/<basename>_results.txt` or equivalent); Guinier/Kratky/log-log from plots step data or from curve data (same transforms as in per-profile plots). No new pipeline steps; summary report is assembled from existing outputs.
- **Implementation:** New or extended function in `autosaxs.report` (e.g. `build_summary_report_pdf(summary_data, output_path)` or equivalent). No EventBus events. Only include a section if the corresponding data is available (e.g. no descriptors table if simple_analysis was not in steps; no Guinier/Kratky/log-log panels if plots was not in steps or data is missing).

---

## 7. Configuration parameters

All of the following are read from the config (YAML) via `context[key1, key2, ...]` or used in `context.update_config`:

- **steps** — List of step names. From EventBus (pipeline/step selection).
- **directory** — Working directory. From interface (DIRECTORY_SPECIFIED).
- **calibrant_name** — String (e.g. `'AgBh'`). Used by calibration and `get_interring_dist_px`.
- **center_refinement** — Dict with at least: `q_start`, `q_stop`, `min_segment_len`.
- **detector_geometry** — Dict with at least: `dist`, `wavelength`, `pixel_size` (sequence of two numbers), `rot1`, `rot2`, `rot3`.
- **ring_search** — Dict with at least: `q_stop`, `ring_I_threshold`, `r_max_px`, `r_step_px`.
- **r_beam_px** — Beam radius in pixels (number).
- **mask_config** — Dict with at least `mode`: one of `'auto'`, `'from_file'`, `'combined'`.
- **refined** — Written by calibration: refined geometry parameters and `wavelength`.
- **sub** — Dict with at least `q_range_abs` (used for buffer subtraction tail-matching; can be `None` to use relative range).
- **bodies** — Dict with `q_range_nm` and `q_range_channels` (used to derive first/last channel for BODIES; exactly one of the two ranges is used in practice; if `q_range_nm` is set, channels are computed from the 1D curve’s `q`).
No default values are defined in the controller for these; they must exist in config when the corresponding step or calibration runs, or the code will raise.

---

## 8. Calibration

- **Inputs:** Calibrant 2D path (pattern `raw/*_calib.tif`), optional mask path (if `mask_config.mode` is `from_file` or `combined`), and config (calibrant_name, center_refinement, detector_geometry, ring_search, r_beam_px, mask_config).
- **Results:**
  - `directory/calibration.png` — calibration plot.
  - `directory/calibration_mask.png` — mask visualization.
  - `directory/integrator_params/` — `ai_params.json`, `detector_params.json`, and optionally `mask.npy`.
  - Config updated with `refined` (and `context['refined']` set).
- **Fast-forward:** If `fast_forward=True` and config has key `refined` and both `calibration.png` and `directory/integrator_params` exist, calibration is skipped and the integrator is loaded from disk; otherwise calibration runs (or returns `integrator: None, refined: None` if no calibrant path).
- **Behavior:** Center refinement → ring identification → geometry refinement (processor’s `find_center`, `find_rings`, `refine`); mask applied per `mask_config` (auto / from file / combined); integrator saved and calibration/mask figures written.

---

## 9. Steps description

- **integration**  
  - Inputs: integrator (from calibration or from `integrator_params/`), 2D paths.  
  - Outputs: For each 2D file, one 1D file in `averaged/`: `int_<basename>.dat` (basename from original filename). Paths appended to `buffer_2d` (optional, if there were buffer 2D paths), `sample_2d`; 1D paths in memory can later be used for subtraction.  
  - Fast-forward: For each path, if `averaged/int_<basename>.dat` already exists, integration for that file is skipped.

- **subtraction**  
  - Inputs: Pairs (sample_1d_path, buffer_1d_path) from alignment. Config `sub.q_range_abs` for tail-matching.  
  - Outputs: In `subtracted/`: `sub_<basename>.dat`, `diff_<basename>.png`, `sub_<basename>.png` per pair. Basename from sample 1D filename with `int_` prefix stripped.  
  - Fast-forward: If for a pair both `sub_<basename>.dat` and the two PNGs exist, subtraction for that pair is skipped.  
  - Algorithm: `subtract_buffer` in processor (match_tail scaling using config `q_range_abs` or default relative range, whittaker_smooth, then subtract and write).

- **simple_analysis**  
  - Inputs: Profile path (1D). Outputs: `descriptors/<basename>_results.txt`, `<basename>.out` (ATSAS autorg, datgnom).  
  - Fast-forward: If both exist for basename, step skipped.

- **plots**  
  - Inputs: Profile path.  
  - Outputs: In `plots/`: `guinier_<basename>.png`, `kratky_<basename>.png`, `loglog_<basename>.png`, and corresponding `.dat` files (q²/log(I), q vs I·q², log(q)/log(I)).  
  - Fast-forward: If all three PNGs exist, plotting is skipped.

- **mixture** (step 5 in pipeline order, immediately after plots; runs in **second, slow processing** per selected profile)  
  - Inputs: Profile path (1D subtracted curve; q in nm⁻¹, intensity, sigma). Output directory `mixture/mixture_<basename>/`.  
  - Implementation: **`autosaxs.mixture.fit_mixtures(profile_path, output_dir, ...)`** — runs six MIXTURE fits (1-, 2-, 3-phase × Gaussian, Schultz–Zimm; SPHERE-only), computes R², R²_adj, BIC on direct and log-clipped intensities, selects the model with **lowest BIC_log**, writes comparison plot (I vs q and log I vs q), distribution plot (R in nm), and results CSV. Parsing and quality metrics in **autosaxs.mixture**.  
  - Outputs: In `mixture/mixture_<basename>/`: per-run subdirs (e.g. `nph1_Gauss/`, `nph2_Schultz/`, …), plus `mixture_comparison.png`, `mixture_distributions.png`, `mixture_results.csv`. Results include best-model label and BIC_log for reports.  
  - When it runs: Per **selected** profile in the second pass (with polydispfit, bodies, dammif, ai_analysis).  
  - Fast-forward: If output directory already contains the comparison plot, distribution plot, and results CSV for that basename, step is skipped.  
  - Failures: Caught and reported via **MESSAGE**; pipeline continues.

- **polydispfit**  
  - Inputs: Profile path. Fixed in code: q_range (0.1, 5.0) nm⁻¹, model `sphere`, gaussian distribution params/bounds. Depends on tabular lookup table `autosaxs/global/tabular/<model_name>.npz` (e.g. `sphere.npz`).  
  - **Regeneration of .npz:** If the required model .npz file is absent under `autosaxs/global/tabular/`, the step regenerates it (e.g. by running the precalculation that writes the lookup table) before performing the fit. No user prompt; regeneration is automatic when the file is missing.  
  - Outputs: In `polydispfit/polydispfit_<basename>/`: `*_fit_comparison.png`, `*_radius_distribution.png`, `*_fit.dat` (with metadata).  
  - Fast-forward: If those three exist, step is skipped.

- **bodies**  
  - Inputs: Profile path; config `bodies.q_range_nm` or `bodies.q_range_channels`.  
  - Outputs: In `bodies/bodies_<basename>/`: `bodies_fit-<shape>.fir` for each shape in `BODIES_SHAPES`, `<shape>_view.png`, `<basename>_fits.png`, a combined **`bodies_fits.yml`**, and a **`bodies_fits.csv`**.  
  - **`bodies_fits.yml`:** Single YAML file. Dictionary mapping each shape name (e.g. `ellipsoid`, `dumbbell`, …) to a dict of fit parameters and quality: `{shape0: {p0: v0, p1: v1, ... chi2: v_chi2}, shape1: ...}`. Keys are shape identifiers; each value holds all fitted parameters (names and values) and `chi2`.  
  - **`bodies_fits.csv`:** Single CSV. Columns: `q`, `exp`, then one column per fitted shape (e.g. `ellipsoid`, `dumbbell`, …). Rows: experimental q and intensity over the **fitted q-range only** (same q-limits as used for the fits / as on the fits figure), plus each shape’s fitted intensity on that q-grid. 
  - Fast-forward: If all `bodies_fit-<shape>.fir` exist, `<basename>_fits.png` exists, and both `bodies_fits.yml` and `bodies_fits.csv` exist, step is skipped.

- **dammif**  
  - Inputs: Profile path and GNOM `.out` path (from simple_analysis).  
  - Precondition: Step `simple_analysis` must be in steps.  
  - Outputs: In `dammif/dammif_<basename>/`: `dammif-<i>.fir`, `dammif-<i>-1.cif`, view/fits PNGs, a combined **`dammif_fits.yml`**, and a **`dammif_fits.csv`**. Number of replicates is fixed in code: 2.  
  - **`dammif_fits.yml`:** Single YAML file. DAM provides no fit descriptors by default; each replicate is described by computed descriptors. Dictionary mapping each replicate (e.g. `dammif-0`, `dammif-1`) to a dict: `{dammif-<i>: {Rg: ..., Dmax: ..., V: ..., f_ratio: ..., chi2: ...}, ...}`. **Rg** = gyration radius; **Dmax** = maximum dimension; **V** = volume = N × V(atom) (N = number of dummy atoms, V(atom) = volume per atom); **f_ratio** = Rg / Dmax.  
  - **`dammif_fits.csv`:** Single CSV. Same structure as bodies: columns `q`, `exp`, then one column per replicate (e.g. `dammif-0`, `dammif-1`). Rows: experimental q and intensity over the **fitted q-range only** (same q-limits as on the fits figure), plus each replicate’s fitted intensity on that q-grid.
  - Fast-forward: If all replicate `.fir` files exist (`dammif-1.fir`, `dammif-2.fir`, …), `<basename>_fits.png` exists, and both `dammif_fits.yml` and `dammif_fits.csv` exist, step is skipped.

- **ai_analysis**  
  - Inputs: ATSAS results path, list of plot paths (sub, guinier, kratky, loglog).  
  - Preconditions: Exactly one selected profile; `simple_analysis` and `plots` in steps.  
  - Outputs: In `ai_analysis/`: `<basename>_context.txt` (text + vision descriptions), `<basename>_llm_answer.txt`. Uses prompts under `PROMPTS_DIR` (`autosaxs/prompts/visual/saxs_1d.txt`, guinier_plot.txt, kratky_plot.txt, loglog_plot.txt) and asks user for a query, then calls LLM via **autosaxs.foreign.aiAssistantFramework.lib.llm** (vendored; no external aiAssistantFramework).  
  - Fast-forward: If `_context.txt` exists, vision part is skipped; if `_llm_answer.txt` exists, LLM call is skipped.

---

## 10. Error handling and validation

- **Before processing:** Images: patterns e.g. `*_calib.tif`; no explicit extension check. Mask: read and cast to bool; bad extension → `RuntimeError` in `read_mask`. 1D: must parse with `read_saxs`; invalid → from `read_data`.
- **Before calibration:** Directory set and exist. No calibrant → `autocalib` returns `integrator: None, refined: None`. Config must have calibrant_name, center_refinement, detector_geometry, ring_search, r_beam_px, mask_config.
- **Integration without calibration:** Needs `integrator_params/` with `ai_params.json`, `detector_params.json`, `mask.npy`. Missing → keep prompting; multiple `mask.*` → processor raises.

- **Alignment (buffer–sample):** Overlapped or unpaired → messages and retry (sleep 10 s). On subtraction, alignment rechecked; if still invalid, `RuntimeError` with overlapped/not_paired.

- **Calibration/processing failure:** No try/except around `autocalib`, `integrate`, `subtract`; exceptions propagate. Steps **simple_analysis**, **plots**, **mixture**, **polydispfit**, **bodies**, **dammif**, **ai_analysis** are each wrapped in try-except: any exception during the step is caught, reported to the user via the EventBus (**MESSAGE** with failure text), and the pipeline continues (next profile or next step); no propagation.

- **User interruption:** Interface publishes **PROGRAM_INTERRUPTED** (e.g. empty obligatory input or quit). Controller exits (e.g. raise `PipelineInterrupt`); not caught in main loop.
- **Script-based API (§3.2):** Required files must exist under the given directory; missing file → API raises. Alignment-failure MESSAGE → `fast_first_processing` raises.

---

## 11. Invariants and edge cases

- **EventBus wiring:** Controller has EventBus + viewer; one interface (CLI, GUI, or API) connects. All I/O via events.
- **Naming (buffer–sample):** Buffer ends with `_buffer<.ext>`, sample with `_sample<.ext>`. Pair when buffer base is **contained in** sample base. One sample ↔ multiple buffers = “overlapped”; unpaired = error. Loop requires no overlapped, no unpaired.
- **LATEST_STEPS_PATH (within autosaxs):** Derived from the package directory `_autosaxs_dir`, e.g. `os.path.join(_autosaxs_dir, "temp", "latest_steps.yml")`.
- **Empty selections:** All steps unchecked → `default_steps`. No profile selected → empty dict; per-profile loop runs zero times.
- **Profile selection (§4 step 7):** **PROFILE_SELECTION_REQUESTED** only if at least one step after `simple_analysis`/`plots` (mixture, polydispfit, bodies, dammif, ai_analysis). Otherwise skip to “Upload more data?” .
- **Alignment order:** `buffer_paths_1d` from `aligned_pairs` then `list(set(...))`; buffer order not preserved; subtraction iterates `aligned_pairs`.
- **Subtraction output:** `averaged/int_foo_sample.dat` → basename `foo_sample`, output `subtracted/sub_foo_sample.dat` (`int_` stripped once).
- **dammif:** 2 replicates; fixed in code.
- **polydispfit:** q_range (0.1, 5.0), sphere+gaussian; hardcoded.
- **ai_analysis:** Exactly one profile; `plot_paths` 4-tuple (sub, guinier, kratky, loglog); model names hardcoded.
- **Config filename:** Requested as “config.conf”; content YAML.
- **FILE_REQUESTED:** `skip_if_exists=True` → may return immediately if files exist. `except_prev_paths` excludes listed paths so “new” files are preferred across iterations.
