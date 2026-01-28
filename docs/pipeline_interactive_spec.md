# Technical specification: `Controller.pipeline_interactive`

Specification of `Controller.pipeline_interactive` in `repos/saxs_controller.py`: single source of truth for implementation and refactoring.

---

## 1. Purpose and scope

**Purpose:** Interactive SAXS pipeline: detector calibration; 2D→1D integration of buffer/sample images; optional buffer subtraction; descriptors (Rg, I(0), P(R) via ATSAS); Guinier/Kratky/log-log plots; optional shape fitting (BODIES, DAMMIF, polydisperse spheres); optional AI analysis. File-based I/O with directory and file upload prompts.

---

## 2. Technological stack

- **Technologies:** Python 3; YAML for config and metadata; CSV for tabular data in 1D curve files; JSON for integrator params and GUI protocol.
- **External packages (as used in this pipeline):**
  - **ATSAS** — Software package for SAXS processing. It is responsible for descriptors calculation, fittng with geometric primitives (BODIES component of ATSAS), "ab initio" fitting with Dummy Atom Model (DAMMIF component of ATSAS)
  - **customtkinter** — Used by `gui_interface.py` (former `gui.py`) for pipeline/step selection, profile selection, and short-lived dialogs per query.
  - **matplotlib** — Plotting (viewer, gui).
  - **numpy, pandas** — Arrays and tabular data.
  - **scipy** — e.g. `scipy.special.gamma`, interpolation; **scipy.ndimage** in processor.
  - **pyFAI** — Calibration and integration (`pyFAI`, `pyFAI.calibrant`, `pyFAI.detectors.Pilatus1M`, `pyFAI.geometryRefinement.GeometryRefinement`, `pyFAI.io.image` for reading images).
  - **fabio** — Reading `.msk` mask files.
  - **ase** — Atoms and structure (e.g. `ase.io.read`, `ase.Atoms`) for viewer and CIF from DAMMIF.
  - **yaml** — Config and metadata.
  - **aiAssistantFramework.lib.llm** — LLM requests for AI analysis.
  - **polydispfit** — Polydisperse sphere fitting (project-local module).
- **Project-internal modules and usage:**
  - **processor** (`repos/processor.py`): `IntegratorExtended`, `integrate_2d_to_1d`, `subtract_buffer`, `find_center`, `find_rings`, `refine`, `get_detector`, `get_interring_dist_px` (and `*` import so e.g. mask/geometry helpers used by calibration). Calibration and integration logic live here.
  - **utils** (`repos/utils.py`): `read_from_tiff` (via processor’s use of `utils`); `read_saxs`, `write_saxs`, `write_data`, `read_data`, `load_config`, `save_config`, `get_pipeline_paths`, `map_sample_files_to_buffer_files`, `read_bodies_cif`, `calc_chi2`, `calculate_atoms_density_and_isosurface`, `calculate_shape_density_and_isosurface`; `ROOT_DIR`, `REPO_DIR`; `ENV` (from `global/env.yml`) for `ATSAS_BIN_PREFIX`; `whittaker_smooth` (from `supervised_ml`) used by subtraction.
  - **interface** (`repos/interface.py`): Defines the abstract `Interface` class (user I/O contract). Implementations live in `cli_interface.py` and `gui_interface.py`; only one is used at a time.
  - **event_bus** (`repos/event_bus.py`): `EventBus` and event type enum. See §3.1 for catalogue and payloads.
  - **cli_interface** (`repos/cli_interface.py`): CLI implementation of `Interface`; same event contract as GUI. stdin/print and file monitoring; pipeline/step and profile selection in this module.
  - **gui_interface** (`repos/gui_interface.py`): GUI implementation of `Interface`; same event contract as CLI. Short-lived CustomTkinter dialogs per query; pipeline/step and profile selection as dialogs.
  - **viewer** (`repos/viewer.py`): Visualization (`view_calibration`, `view_mask`, `view_curves`, `plot_3d_views_and_scattering`, etc.); pipeline uses `PLTViewer` in `__main__`.
  - **context** (`repos/context.py`): `Context` — holds directory, config, and path groups; config and path accessors.

---

## 3. Architecture

- **Type of application:** Interactive pipeline with CLI or GUI (one chosen); both communicate with Controller only via EventBus. File-based working directory; app waits for files matching patterns.
- **Module layout:** **`interface.py`** (abstract `Interface`), **`event_bus.py`** (`EventBus` + event enum), **`cli_interface.py`**, **`gui_interface.py`**. One EventBus instance; Controller and one Interface implementation connect to it.
- **Layers:**
  - **EventBus** (`event_bus.py`): Single channel for Controller–Interface I/O; no direct calls. Event enum and payloads in `event_bus.py`; semantics in §3.1.
  - **Interface** (abstract in `interface.py`; implementations in `cli_interface.py`, `gui_interface.py`): Same event contract; exactly one implementation used.
    - **CLI** (`cli_interface.py`): Subscribes to **FILE_REQUESTED**, **CHOICE_REQUESTED**, **DIRECTORY_REQUESTED**, **MESSAGE**; publishes **FILE_UPLOADED**, **FILE_UPLOAD_CANCELED**, **OPTION_CHOSEN**, **OPTION_CHOICE_CANCELED**, **DIRECTORY_SPECIFIED**, **PROGRAM_INTERRUPTED**. stdin/print + file monitoring; pipeline/step and profile selection in-module.
    - **GUI** (`gui_interface.py`): Same events. Short-lived dialogs (directory, file, choice, message); pipeline/step and profile selection as dialogs.
  - **Controller** (`saxs_controller.py`): Publishes to EventBus, subscribes to responses; calls processor, context, viewer, step logic. Holds EventBus (and viewer, etc.), not Interface.
  - **Processing** (`processor.py`), **Viewer** (`viewer.py`), **Context** (`context.py`), **Utils** (`utils.py`): Roles unchanged (calibration/integration, plotting, config/paths, I/O and helpers).
- **How `pipeline_interactive` wires components:**
  1. Create EventBus; Controller and one of `cli_interface` / `gui_interface` connect to it. Create `Context()`.
  2. If not `all_from_config`: **DIRECTORY_REQUESTED** → **DIRECTORY_SPECIFIED** or **PROGRAM_INTERRUPTED**; then **FILE_REQUESTED** for `config.conf` → **FILE_UPLOADED** or cancel. Load config. Steps via EventBus (pipeline/step selection). Else: load config from path; read `steps`, `directory`.
  3. If `calibration` in steps: **FILE_REQUESTED** (calibrant) → response; if not `all_from_config`, **CHOICE_REQUESTED** (mask mode) → **OPTION_CHOSEN** / cancel; if mask from file/combined, **FILE_REQUESTED** (mask) → response. Run `autocalib(...)` or load integrator from disk when `integration` without calibration.
  4. Main loop: **FILE_REQUESTED** (buffer/sample 2D or 1D, or `subtracted/*.dat`) → **FILE_UPLOADED** or cancel; on alignment errors **MESSAGE** then retry. Profile selection via EventBus → `selected_profiles`. For each profile run optional steps (descriptors, plot, polydispfit, bodies, dammif, ai_analysis). **CHOICE_REQUESTED** (“Upload more data?”) → **OPTION_CHOSEN** “no” or cancel to exit.
  5. Return `context`. On **PROGRAM_INTERRUPTED**, exit (e.g. raise `PipelineInterrupt`).

### 3.1 EventBus: events and data flow

- **Channel:** Only path for Controller–Interface I/O. Enum and payload types in `event_bus.py`. One response per request (uploaded / canceled / interrupted). “Interface” below = connected implementation (`cli_interface` or `gui_interface`).
- **Events and payloads:**
  - **DIRECTORY_REQUESTED** — Controller. Payload: `query` (str). → **DIRECTORY_SPECIFIED** or **PROGRAM_INTERRUPTED**.
  - **DIRECTORY_SPECIFIED** — Interface. `path` (str).
  - **FILE_REQUESTED** — Controller. `directory`, `query`, `filepattern`, `obligatory`, `skip_if_exists`, `except_prev_paths`, `allow_same_time`, … (semantics as former `wait_for_file`). → **FILE_UPLOADED** or **FILE_UPLOAD_CANCELED** (or **PROGRAM_INTERRUPTED** if obligatory).
  - **FILE_UPLOADED** — Interface. `paths` (list of str).
  - **FILE_UPLOAD_CANCELED** — Interface. optional `reason`. If obligatory, may send **PROGRAM_INTERRUPTED** instead/additionally.
  - **CHOICE_REQUESTED** — Controller. `query`, `options` (key→label), `default_op`. → **OPTION_CHOSEN** or **OPTION_CHOICE_CANCELED** or **PROGRAM_INTERRUPTED**.
  - **OPTION_CHOSEN** — Interface. `choice` (str, key from `options`).
  - **OPTION_CHOICE_CANCELED** — Interface. optional payload; may add **PROGRAM_INTERRUPTED** if required.
  - **MESSAGE** — Controller. `text` (str). Interface displays; no response.
  - **PROGRAM_INTERRUPTED** — Interface. optional `reason`. Controller must exit pipeline.
- **Optional extensions:** **STATUS_UPDATE**, **ERROR_REPORT**. Sync vs async: implementation-defined; one logical response per request; **PROGRAM_INTERRUPTED** always exits.

---

## 4. User workflow (step-by-step)

- **File inputs:**
  - **Config:** Required. Filename requested: `config.conf` (content is YAML). Must be in the chosen working directory (or provided path when `all_from_config`).
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
  1. Directory and config: user provides directory (or config path in batch mode); config file `config.conf` is loaded.
  2. If `calibration` in steps: wait for calibrant; if mask mode is from_file or combined, wait for mask; run `autocalib` → writes `integrator_params/`, `calibration.png`, `calibration_mask.png`, and updates config with `refined`.
  3. If `integration` in steps and not `calibration`: wait until directory contains `integrator_params` with `ai_params.json`, `detector_params.json`, `mask.npy`; then load `IntegratorExtended` from disk.
  4. Main cycle:
     - If `integration` in steps: wait for buffer (if `subtraction` in steps) and sample 2D files; align sample↔buffer by name; integrate all buffer and sample images → 1D in `averaged/`; add paths to context.
     - If `subtraction` in steps and `integration` not in steps: wait for buffer and sample 1D in `averaged/`; align; set `buffer_paths_1d` and `sample_paths_1d` (and basename list from sample names).
     - If `subtraction` in steps: align `sample_paths_1d` with `buffer_paths_1d`; for each aligned pair run `subtract` → outputs in `subtracted/`; extend context with buffer_1d and sample_1d paths.
     - If `subtraction` not in steps: wait for `subtracted/*.dat`; for each file load, plot, and build basename list and profile paths.
  5. Profile selection: unless `all_from_config`, build `profiles_data` and request via EventBus; result is `selected_profiles` (dict basename → profile).
  6. For each selected profile, in order: `simple_analysis` → descriptors and GNOM; `plots` → Guinier, Kratky, log-log; `polydispfit`; `bodies_fit`; `dammif_fit` (requires `simple_analysis`); `ai_analysis` (requires exactly one selected profile, `simple_analysis`, and `plots`). Each step may write to a subdir under the working directory and append paths to context.
  7. Ask “Upload more data?”; if answer does not start with “n”, repeat from step 4 (new buffer/sample or profiles); otherwise exit and return context.

- **Processing:** When `integration` in steps, 2D files are integrated in one go after alignment. Subtraction runs in the same cycle after integration (or after loading 1D files); pairing via `map_sample_files_to_buffer_files` (see §10 naming).

---

## 5. Data and file formats

- **2D images:** Read with `pyFAI.io.image.read_image_data` (in `utils.read_from_tiff`). Patterns in the app: `raw/*_calib.tif`, `raw/*_buffer.tif`, `raw/*_sample.tif` — effectively TIFF (`.tif`).
- **Mask:** Extensions: `.npy` (NumPy load, cast to bool), `.txt` (loadtxt, cast to bool), `.msk` (fabio, cast to bool, then flipped on first axis). Values must be 0/1 or boolean-like; no additional validation in code.
- **1D SAXS curves (read_saxs/write_saxs):** Format is the generic “YAML metadata + CSV data” used by `read_data`/`write_data` in `utils.py`: file starts with a YAML block between `---` and `...`, then a line `# Data in CSV format\n`, then CSV. For SAXS, CSV has columns `q`, `intensity`, and optionally `sigma`. Metadata is a dict. Any file path passed to `read_saxs` must conform to this layout.
- **Config file:** Working directory, `config.conf`; YAML. Keys in §6; schema implied by controller/processor use.
- **Integrator persistence:** `integrator_params/` must contain `ai_params.json`, `detector_params.json`, and optionally `mask.npy` (one file matching `mask.*`). Used when step `integration` is selected without `calibration`.

---

## 6. Configuration parameters

All of the following are read from the config (YAML) via `context[key1, key2, ...]` or used in `context.update_config`:

- **steps** — List of step names. From config when `all_from_config=True`; else from EventBus (pipeline/step selection).
- **directory** — Working directory. From config when `all_from_config=True`; else from interface.
- **calibrant_name** — String (e.g. `'AgBh'`). Used by calibration and `get_interring_dist_px`.
- **center_refinement** — Dict with at least: `q_start`, `q_stop`, `min_segment_len`.
- **detector_geometry** — Dict with at least: `dist`, `wavelength`, `pixel_size` (sequence of two numbers), `rot1`, `rot2`, `rot3`.
- **ring_search** — Dict with at least: `q_stop`, `ring_I_threshold`, `r_max_px`, `r_step_px`.
- **r_beam_px** — Beam radius in pixels (number).
- **mask_config** — Dict with at least `mode`: one of `'auto'`, `'from_file'`, `'combined'`.
- **refined** — Written by calibration: refined geometry parameters and `wavelength`.
- **sub** — Dict with at least `q_range_abs` (used for buffer subtraction tail-matching; can be `None` to use relative range).
- **bodies** — Dict with `q_range_nm` and `q_range_channels` (used to derive first/last channel for BODIES; exactly one of the two ranges is used in practice; if `q_range_nm` is set, channels are computed from the 1D curve’s `q`).
- **paths.selected_profiles** — When `all_from_config=True`, skip profile-selection request; must contain profile dicts with at least `path` and `plot_path`.

No default values are defined in the controller for these; they must exist in config when the corresponding step or calibration runs, or the code will raise.

---

## 7. Calibration

- **Inputs:** Calibrant 2D path (pattern `raw/*_calib.tif`), optional mask path (if `mask_config.mode` is `from_file` or `combined`), and config (calibrant_name, center_refinement, detector_geometry, ring_search, r_beam_px, mask_config).
- **Results:**
  - `directory/calibration.png` — calibration plot.
  - `directory/calibration_mask.png` — mask visualization.
  - `directory/integrator_params/` — `ai_params.json`, `detector_params.json`, and optionally `mask.npy`.
  - Config updated with `refined` (and `context['refined']` set).
- **Fast-forward:** If `fast_forward=True` and config has key `refined` and both `calibration.png` and `directory/integrator_params` exist, calibration is skipped and the integrator is loaded from disk; otherwise calibration runs (or returns `integrator: None, refined: None` if no calibrant path).
- **Behavior:** Center refinement → ring identification → geometry refinement (processor’s `find_center`, `find_rings`, `refine`); mask applied per `mask_config` (auto / from file / combined); integrator saved and calibration/mask figures written.

---

## 8. Steps description

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
  - Inputs: Profile path (1D).  
  - Outputs: In `descriptors/`: `<basename>_results.txt` (AUTORG + MW from Rg) and `<basename>.out` (GNOM P(R)). Uses ATSAS `autorg` and `datgnom`; prefix from `utils.ENV["ATSAS_BIN_PREFIX"]`.  
  - Fast-forward: If both `*_results.txt` and `*.out` exist for that basename, step is skipped.

- **plots**  
  - Inputs: Profile path.  
  - Outputs: In `plots/`: `guinier_<basename>.png`, `kratky_<basename>.png`, `loglog_<basename>.png`, and corresponding `.dat` files (q²/log(I), q vs I·q², log(q)/log(I)).  
  - Fast-forward: If all three PNGs exist, plotting is skipped.

- **polydispfit**  
  - Inputs: Profile path. Fixed in code: q_range (0.1, 5.0) nm⁻¹, model `sphere`, gaussian distribution params/bounds.  
  - Outputs: In `polydispfit/polydispfit_<basename>/`: `*_fit_comparison.png`, `*_radius_distribution.png`, `*_fit.dat` (with metadata).  
  - Fast-forward: If those three exist, step is skipped.

- **bodies**  
  - Inputs: Profile path; config `bodies.q_range_nm` or `bodies.q_range_channels`.  
  - Outputs: In `bodies/bodies_<basename>/`: `bodies_fit-<shape>.fir` for each shape in `BODIES_SHAPES`, `<shape>_view.png`, and `<basename>_fits.png`.  
  - Fast-forward: If all `bodies_fit-<shape>.fir` exist and `<basename>_fits.png` exists, step is skipped. Note: BODIES is invoked without `--first`/`--last` in the current code (commented out).

- **dammif**  
  - Inputs: Profile path and GNOM `.out` path (from simple_analysis).  
  - Precondition: Step `simple_analysis` must be in steps.  
  - Outputs: In `dammif/dammif_<basename>/`: `dammif-<i>.fir`, `dammif-<i>-1.cif`, and view/fits PNGs. Number of replicates is fixed in code: 2.  
  - Fast-forward: If all `dammif-<i>.fir` exist and `<basename>_fits.png` exists, step is skipped.

- **ai_analysis**  
  - Inputs: ATSAS results path, list of plot paths (sub, guinier, kratky, loglog).  
  - Preconditions: Exactly one selected profile; `simple_analysis` and `plots` in steps.  
  - Outputs: In `ai_analysis/`: `<basename>_context.txt` (text + vision descriptions), `<basename>_llm_answer.txt`. Uses prompts under `PROMPTS_DIR` (`prompts/visual/saxs_1d.txt`, guinier_plot.txt, kratky_plot.txt, loglog_plot.txt) and asks user for a query, then calls LLM.  
  - Fast-forward: If `_context.txt` exists, vision part is skipped; if `_llm_answer.txt` exists, LLM call is skipped.

---

## 9. Error handling and validation

- **Before processing:** Images: patterns e.g. `*_calib.tif`; no explicit extension check. Mask: read and cast to bool; bad extension → `RuntimeError` in `read_mask`. 1D: must parse with `read_saxs`; invalid → from `read_data`.
- **Before calibration:** Directory set and exist. No calibrant → `autocalib` returns `integrator: None, refined: None`. Config must have calibrant_name, center_refinement, detector_geometry, ring_search, r_beam_px, mask_config.
- **Integration without calibration:** Needs `integrator_params/` with `ai_params.json`, `detector_params.json`, `mask.npy`. Missing → keep prompting; multiple `mask.*` → processor raises.

- **Alignment (buffer–sample):** Overlapped or unpaired → messages and retry (sleep 10 s). On subtraction, alignment rechecked; if still invalid, `RuntimeError` with overlapped/not_paired.

- **Calibration/processing failure:** No try/except around `autocalib`, `integrate`, `subtract`, or ATSAS/shell; exceptions propagate. BODIES: if no `.fir` produced, message and return subdir; loop continues.

- **User interruption:** Interface publishes **PROGRAM_INTERRUPTED** (e.g. empty obligatory input or quit). Controller exits (e.g. raise `PipelineInterrupt`); not caught in main loop.

---

## 10. Invariants and edge cases

- **EventBus wiring:** Controller gets EventBus + viewer (no Interface ref). One of `cli_interface` / `gui_interface` gets the same EventBus. All I/O via events.
- **Naming (buffer–sample):** Buffer ends with `_buffer<.ext>`, sample with `_sample<.ext>`. Pair when buffer base is **contained in** sample base. One sample ↔ multiple buffers = “overlapped”; unpaired = error. Loop requires no overlapped, no unpaired.
- **LATEST_STEPS_PATH:** Controller writes `utils.ROOT_DIR/temp/latest_steps.yml`. If `gui_interface` uses a hardcoded path (e.g. `~/KurchatovCoop/temp/...`) for “latest configuration”, it may not see steps when `ROOT_DIR` differs; use `utils.ROOT_DIR`.
- **all_from_config:** Requires `config_path`; config has `steps`, `directory`. No mask question; `context['paths','selected_profiles']` must be pre-filled.
- **Empty selections:** All steps unchecked → fallback to `default_steps`. No profile selected → empty dict; per-profile loop runs zero times.
- **Alignment order:** `buffer_paths_1d` from `aligned_pairs` then `list(set(...))`; buffer order not preserved; subtraction iterates `aligned_pairs`.
- **Subtraction output:** `averaged/int_foo_sample.dat` → basename `foo_sample`, output `subtracted/sub_foo_sample.dat` (`int_` stripped once).
- **dammif:** 2 replicates; fixed in code.
- **polydispfit:** q_range (0.1, 5.0), sphere+gaussian; hardcoded.
- **ai_analysis:** Exactly one profile; `plot_paths` 4-tuple (sub, guinier, kratky, loglog); model names hardcoded.
- **Config filename:** Requested as “config.conf”; content YAML.
- **FILE_REQUESTED:** `skip_if_exists=True` → may return immediately if files exist. `except_prev_paths` excludes listed paths so “new” files are preferred across iterations.
