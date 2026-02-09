# fast2dprocess_gui — Technical Specification

This document is derived solely from the code in this repository. It describes behavior, constraints, and edge cases precisely enough for a new developer or agent to implement or refactor the application without missing requirements.

---

## 1. Purpose and scope

**One-sentence summary:** The application is a desktop GUI for SAXS (Small-Angle X-Ray Scattering) that performs 2D → 1D azimuthal integration with calibrant-based geometry calibration, optional mask and buffer subtraction, with all results automatically saved to a user-selected working directory.

**Main user goal:** Choose an empty working directory at launch, then run calibration and process buffer/samples; all outputs are written automatically to that directory.

---

## 2. Technological stack

- **UI:** CustomTkinter (`customtkinter`), TkinterDnD2 (`tkinterdnd2`) for drag-and-drop.
- **Plotting:** Matplotlib (FigureCanvasTkAgg, Figure, pyplot, colormaps).
- **Numerics / science:** NumPy, SciPy (ndimage, zoom; `scipy.spatial.distance.cdist`; `whittaker_smooth` from **autosaxs.foreign.supervised_ml** (vendored; no external supervised_ml) used in `processor.subtract_buffer`), scikit-learn (`DBSCAN` in `processor.find_center`).
- **X-ray / calibration:** pyFAI (AzimuthalIntegrator, calibrant, geometry refinement, detectors), fabio (images/masks).
- **Config / data:** YAML (`yaml.safe_load` / `yaml.dump`), JSON (calibration service config and status), pandas (in `utils` for SAXS read/write).
- **Project-internal modules:**
  - **processor** (`autosaxs/processor.py`): `autocalib`, `IntegratorExtended`, `integrate_2d_to_1d`, `subtract_buffer`. Used for calibration, integration, and buffer subtraction.
  - **utils** (`autosaxs/utils.py`): `read_from_tiff`, `read_saxs`, `write_saxs`, `whittaker_smooth`. Used for image/curve I/O and subtraction smoothing.

---

## 3. Architecture

- **Application kind:** Single-window desktop GUI (Tk/CustomTkinter) with drag-and-drop, event-driven updates, and background work (calibration subprocess, processing threads).

- **Layers:**
  - **GUI:** `fast2dprocess_gui/gui/` — main window, control panel, 2D tab, 1D tab, widgets. Owns user input, display, and calling into services/managers; schedules UI updates on the main thread. All visual styling (fonts, colors, spacing) must be taken from the style module; no hardcoded style values in GUI code.
  - **Services:** `fast2dprocess_gui/services/` — `CalibrationService`, `ProcessingService`. Orchestrate calibration (subprocess + status monitoring) and processing (process_image, create_subtracted_curve); publish events and call status callbacks.
  - **Managers / models:** `fast2dprocess_gui/models/` — `ConfigManager`, `DataManager`, `CalibrationManager`, `ProcessingManager`. Own state and business rules.
  - **Core:** `fast2dprocess_gui/core/` — event bus (`EventBus`, `EventType`), constants (paths, units), interfaces (`IConfigManager`, `ICalibrationManager`, `IStatusReporter`).
  - **Style:** The module `fast2dprocess_gui/core/style.py` is the single source of truth for all visual style: the CustomTkinter color theme (e.g. `COLOR_THEME`: "blue", "green", "dark-blue"), fonts, colors (including status bar colors), spacing, and any other theme-related variables. This module is the only place where such values are defined; all GUI code and any code that sets widget appearance or theme must import from it (e.g. `from fast2dprocess_gui.core.style import FONTS, COLORS, STATUS_COLORS, COLOR_THEME`) and must not hardcode theme, fonts, colors, or other style constants elsewhere. Status bar colors are defined here.

- **Ownership:**
  - **DataManager:** Single path per file type for calibrant, buffer, and mask; **sample** holds a **list of paths** (zero or more). Validation: image extension `.tif`/`.tiff`; mask extension `.npy`/`.txt`/`.msk` and 0/1 or boolean-like values. Copy images to working directory; all outputs are written to the working directory (selected at launch, must be empty). Only the sample field accepts and stores multiple files.
  - **CalibrationManager:** Calibration state (integrator, `calibrated_params`). Build config for `autocalib`; save integrator and write `calibrated_params` into config (and to disk as needed).
  - **ProcessingManager:** Calls `integrate_2d_to_1d` and `subtract_buffer`; requires `CalibrationManager.is_calibrated`; uses `CalibrationManager.get_integrator()` and `utils.read_from_tiff`; output paths via `filename_utils.generate_filename` into working directory.
  - **ConfigManager:** `basic_params` (wavelength, detector_distance, pixel_size, beam_center_x/y, detector_tilt, tilt_plane_rotation, calibrant_name, r_beam_px, detector_name) and `advanced_params` (center_refinement, ring_search, mask_config). Load/save YAML at `CONFIG_PATH`; on disk: `config_dictionary` and `advanced_params`.

- **Main window (`SAXSProcessorGUI`):** At startup, shows a pop-up directory selection dialog; the user must select an empty working directory (mandatory; app does not proceed until a valid empty directory is chosen or user cancels and exits). Creates `EventBus`; instantiates `ConfigManager`, `DataManager`, `CalibrationManager`, `ProcessingManager`, then `CalibrationService` and `ProcessingService` with shared managers and event bus. Keeps `config_dictionary` as a reference to `config_manager.basic_params` for the control panel. Builds left `ControlPanel` and right tabbed area (2D Images, 1D Curves) plus status bar; subscribes to `CALIBRATION_COMPLETE`, `CALIBRATION_ERROR`, `PROCESSING_COMPLETE`; wires file drop and Apply Calibration to callbacks that call services/managers and update GUI (including scheduling work on main thread where required). All results are written automatically to the working directory; there is no Save button.

---

## 4. User workflow (step-by-step)

0. **Working directory (mandatory at launch)**
   - On startup, before the main window is shown (or immediately when the app starts), a pop-up file selection dialog asks the user to choose a directory. This directory is the **working directory**.
   - **Requirement:** The selected directory must be **empty** (e.g. `os.listdir(path)` must be empty). If the user selects a non-empty directory, the app shows an error and re-prompts. The step is **mandatory**: the app does not proceed to the main workflow until a valid empty directory is selected or the user cancels (in which case the app exits).
   - Once set, all outputs (config, integrator, copied images, integrated/subtracted `.dat` files, plots) are written automatically to this directory.

1. **File inputs**
   - **Calibrant:** Required for calibration. Accepted after validation (see below). Only one path; last drop wins.
   - **Mask:** Optional. Same single-path model; accepted if validation passes.
   - **Buffer image:** Optional. Single path; validated as image.
   - **Sample image(s):** Optional. **Accepts multiple images** in a single drop (or multiple paths from one drop). All valid image paths from the drop are stored as the sample list, **replacing** any previous sample list. Each file is validated as image. Only the sample zone accepts multiple files; calibrant, mask, and buffer do not and an error is displayed in status if one attempts to put more than one file there.

2. **Validation**
   - **Images (calibrant, buffer, sample):** Extension must be `.tif` or `.tiff` (case-insensitive); file must exist. On failure: status error, drop labels reset, path not stored. For **sample** (multiple files): each path is validated independently; valid paths are stored as the sample list, invalid ones are skipped; if all fail, status error and sample list is not updated.
   - **Mask:** Extension `.npy`, `.txt`, or `.msk`; file must load; unique values must be only 0/1 or boolean-like (including float near 0/1). Exactly two unique values required. On failure: status error, drop labels reset, path not stored.

3. **Order of operations**
   - User drops calibrant (and optionally mask). No calibration runs until "Apply Calibration" is clicked.
   - User sets calibration parameters (required: wavelength, detector_distance, pixel_size, beam_center_x, beam_center_y) and clicks "Apply Calibration". Calibration runs in a subprocess. On success, GUI is updated from `calibrated_params`, calibrant 2D is shown and its plot saved, and any existing buffer or sample list is auto-processed **sequentially** (see Auto-processing).
   - User may drop buffer or sample(s) before or after calibration. If dropped after calibration is complete, that buffer or those samples are auto-processed immediately (worker thread). If dropped before calibration, processing is not run until after calibration completes (then existing buffer and all samples are processed).

4. **Auto-processing**
   - Triggered when: (a) calibration completes, and buffer_path is set or the sample list is non-empty; (b) user drops a buffer or one or more samples when already calibrated.
   - **Sequential processing:** When calibration completes with buffer and/or samples set, the application must process them **sequentially** in a single worker thread: buffer first (if set), then **each sample in the sample list in order**. When only buffer or only samples are set, that/those are processed in the same thread. The integrator (pyFAI) must not be used concurrently from multiple threads.
   - For each such image: display 2D, run `ProcessingService.process_image`, copy image to working directory with descriptive name, add 1D curve, save 1D main plot and 2D plot. For each **sample**: after a short delay, create subtracted curve using the most recently added buffer curve (if any) and save its plots.

5. **Buffer subtraction**
   - Performed when a sample has just been processed (for **each** sample in the list): subtract the “current” buffer from that sample. The “current” buffer is the most recently added buffer curve (by iteration order in `CurvesTab1D.curves`, reverse order, first buffer found). Formula: sample − scaled buffer (see Processing pipeline). Output path is generated from buffer and sample basenames; subtracted curve is added to 1D tab and its plots saved. If no buffer has been processed yet, no subtraction is created for that sample.

6. **Persistence**
   - All results are written automatically to the working directory as they are produced (config, integrator params, calibration output, copied images, integrated and subtracted `.dat` files, plots).

---

## 5. Data and file formats

- **Images:** `.tif`, `.tiff` (read via `utils.read_from_tiff` → `pyFAI.io.image.read_image_data`).
- **Mask:** `.npy`, `.txt`, `.msk`. Validation: only two unique values, each interpretable as 0 or 1 (or boolean). Loaded in code as boolean (e.g. `np.load(...).astype('bool')`, `IntegratorExtended.read_mask`); `.msk` is flipped on first axis after load.
- **1D curves:** Format read by `utils.read_saxs`: file has YAML metadata block between `---` and `...`, then CSV block after `# Data in CSV format`. CSV columns: `q`, `intensity`, and optionally `sigma`. Written by `utils.write_saxs` (and `processor.integrate_2d_to_1d` / `subtract_buffer` use it for output).
- **Config file:** Path `WORKING_DIR/config.yml` where `WORKING_DIR` is the directory selected by the user at launch. Structure: top-level keys `config_dictionary` (same content as `ConfigManager.basic_params`) and `advanced_params` only. `ConfigManager.save()` may also persist `calibrated_params` when updated after calibration; calibrated parameters are stored in memory and, when saved, in config.
- **Working directory:** Path is chosen by the user at launch via a pop-up directory selection dialog; the directory **must be empty**. Contents (all written automatically as the user works): `config.yml`, `integrator_params/` (detector_params.json, ai_params.json, optional mask.npy), `calibration_config.json`, `calibration_status.json`, `calibration_output/` (subprocess output; then integrator copied to main `integrator_params`), copied images (e.g. `calibrant_<name>.tif`, `buffer_<name>.tif`, `sample_<name>.tif`), integrated `.dat` files (e.g. `int_<basename>.dat`), subtracted `.dat` (e.g. `subtracted_<sample_basename>_<buffer_basename>.dat`), and plots (e.g. `calibrant_2d_<name>.png`, `plot_1d_<name>.png`, guinier/kratky/loglog when user selects those plot types).

---

## 6. Configuration parameters

- **Basic (editable in Control Panel):** Shown as labeled entries and sliders; stored in `ConfigManager.basic_params` and in saved YAML as `config_dictionary`. Units in UI vs internal:
  - Wavelength: Å (UI) → m (internal): × 1e-10.
  - Detector distance: mm → m: × 1e-3.
  - Pixel size: mm → m (single value shown, stored as [value, value]): × 1e-3.
  - Beam center X/Y: pixels; no conversion.
  - Detector tilt, Tilt plane rotation: radians; no conversion.

- **Defaults (from code):** `ConfigManager.basic_params`: wavelength 1.445e-10 m, detector_distance 0.7 m, pixel_size [1.72e-4, 1.72e-4] m, beam_center_x 1024, beam_center_y 1024, detector_tilt 0, tilt_plane_rotation 0, calibrant_name "AgBh", r_beam_px 35, detector_name "Pilatus1M". Slider ranges in Control Panel: wavelength (0.1–3.0 Å), detector distance (100–1000 mm), pixel size (0.05–0.5 mm), beam center X/Y (0–2048), detector tilt and tilt plane rotation (-0.1–0.1 rad).

- **Required for calibration:** wavelength, detector_distance, pixel_size (list of two numbers), beam_center_x, beam_center_y. Validated in `apply_calibration` and in `CalibrationManager.build_calibration_config`.

- **Advanced (not exposed in Control Panel):** In `ConfigManager.advanced_params`: `center_refinement` (q_start 0.95, q_stop 0.995, min_segment_len 50), `ring_search` (q_stop 0.995, ring_I_threshold 80.0, r_max_px 1000, r_step_px 3), `mask_config` (mode "auto", window_size 7, iqr_tol 1.5). When a mask file is provided, mask_config mode is set to "combined" and calc_abnormal_mask False for the calibration run.

- **Not editable in GUI:** calibrant_name, r_beam_px, detector_name are in basic_params and saved/loaded but have no slider/entry in the Control Panel (fixed in code or via config file only).

---

## 7. Calibration

- **Inputs:** Calibrant image path (required), optional mask path, and config built from `CalibrationManager.build_calibration_config` (includes detector_geometry, center_refinement, ring_search, r_beam_px, calibrant_name, mask_config).

- **Subprocess:** Calibration runs in a separate process to avoid NumPy/pyFAI threading issues with the GUI. Invocation: `sys.executable calibration_service.py <config_json> <output_dir> --status-file <status_file>`. Config JSON: calibrant_path, mask_path, config (full calibration dict). Output dir: `WORKING_DIR/calibration_output`. Status file: `WORKING_DIR/calibration_status.json`. The GUI starts a worker thread that launches this subprocess, waits for it, then reads results from `calibration_output/calibration_result.json` and copies `integrator_params` into `WORKING_DIR/integrator_params`.

- **Status reporting:** Status file path: `WORKING_DIR/calibration_status.json`. JSON keys: `message`, `type` (e.g. "progress", "success", "error"), `timestamp`. The GUI polls this file on a 500 ms timer while `status_monitor_running` is True and updates the status bar text and color from `message` and `type`; on "success" or "error" monitoring stops.

- **Outputs:** Integrator saved to `WORKING_DIR/integrator_params` (and in subprocess to `calibration_output/integrator_params`). `calibrated_params` stored in `CalibrationManager`; config may be updated with `calibrated_params` via `ConfigManager.save()` when calibration completes. GUI fields (beam center, detector distance, wavelength, tilts) updated from calibrated values via `update_gui_after_calibration` and conversions in `CONVERSIONS_TO_DISPLAY`.

---

## 8. Processing pipeline

- **2D → 1D:** `ProcessingManager.process_image` calls `integrate_2d_to_1d(integrator, data, npt=1000, destpath=output_path, metadata=metadata)`. Data comes from `read_from_tiff(image_path)`. Output path: `generate_filename(image_path, "int", ".dat", base_dir=working_dir)` → e.g. `int_<basename>.dat` in working directory. Metadata: type (buffer/sample), source_path.

- **Output naming:** `filename_utils.generate_filename(original_path, operation, extension, additional_info=None, base_dir=None)`. Examples: integrated → `int_<basename>.dat`; subtracted → `generate_filename(buffer_path, "subtracted", ".dat", additional_info=sample_basename, base_dir=working_dir)` → `subtracted_<sample_basename>_<buffer_basename>.dat`; calibrant 2D plot → `calibrant_2d_<basename>.png`; curve plot_1d → `plot_1d_<curve_basename>.png`; etc. All `base_dir` usage refers to the working directory.

- **Buffer subtraction:** Implemented in `processor.subtract_buffer(buffer_path, src_path, destpath, method='match_tail', ...)`. Sample = `src_path`, buffer = `buffer_path`. Formula: I_sub = I_sample − scaling_factor × I_buffer. Scaling by `match_tail`: use high-q tail (default q_range_rel (0.8, None), approach_factor 0.98), smooth with whittaker_smooth, then scale so scaled buffer matches sample in that tail. Interpolation of buffer onto sample q-grid if q arrays differ. Triggered after a sample is processed: the most recently added buffer curve (by reverse iteration in `curves_tab_1d.curves`) is used; if none, no subtraction.

- **Threading:** Processing runs in worker threads (`threading.Thread`). When calibration completes with buffer and/or sample list set, a **single** worker runs and processes buffer first (if set), then **each sample in the sample list in order** (sequential processing). When the user drops a buffer or one or more samples after calibration, one worker is started and processes the new buffer (if any) and/or all newly dropped samples in sequence. The shared integrator must never be used from more than one thread at a time. Status and UI updates are scheduled on the main thread via `root.after_idle` or `root.after(100)` / `root.after(200)` so that `_update_status`, `add_curve`, `display_1d_curves`, 2D plot save, and subtraction run on the main thread where needed.

---

## 9. Event bus

- **EventType values (all):** FILE_LOADED, CALIBRATION_STARTED, CALIBRATION_COMPLETE, CALIBRATION_ERROR, PROCESSING_STARTED, PROCESSING_COMPLETE, PROCESSING_ERROR, STATUS_UPDATE, CONFIG_CHANGED.

- **Publishers and subscribers (as implemented):**
  - **CALIBRATION_STARTED:** Published by `CalibrationService.run_calibration` (calibrant_path in data). No subscriber in code.
  - **CALIBRATION_COMPLETE:** Published by `CalibrationService` (calibrated_params, calibrant_path). Subscriber: `SAXSProcessorGUI._on_calibration_complete` (update GUI, show calibrant, copy to working directory, save calibrant plot, auto-process buffer and all samples in sample list).
  - **CALIBRATION_ERROR:** Published by `CalibrationService`. Subscriber: `SAXSProcessorGUI._on_calibration_error` (status bar, reset color after 5 s).
  - **PROCESSING_STARTED:** Published by `ProcessingService.process_image`. No subscriber in code.
  - **PROCESSING_COMPLETE:** Published by `ProcessingService.process_image`. Subscriber: `SAXSProcessorGUI._on_processing_complete` (no-op).
  - **PROCESSING_ERROR:** Published by `ProcessingService.process_image`. No subscriber in code.
  - FILE_LOADED, STATUS_UPDATE, CONFIG_CHANGED: Defined but not published by any code in the traced modules.

---

## 10. GUI layout and behavior

- **Left: Control Panel.** Drag-and-drop zones (in order): Calibrant Image, Mask File (Optional), Buffer Image, Sample Image(s). Calibrant, Mask, and Buffer each accept a **single file**; **Sample** accepts **one or more files** in a single drop (multiple paths replace the previous sample list). Display for Sample when multiple files: "File: <first> + N more". Each zone shows “Drag & Drop … Here” or “File: <filename>” and “No file selected” / “File: <filename>”. Below: “Calibration Parameters” with entries and sliders for wavelength, detector distance, pixel size, beam center X/Y, detector tilt, tilt plane rotation; “Apply Calibration” button. All results are written automatically to the working directory. Callbacks: on_file_drop, on_apply_calibration.

- **Right: Tabs.** “2D Images”: left = scrollable thumbnails (Images); right = main 2D view. “1D Curves”: left = scrollable curve list (checkboxes); top-right = plot type selector; below = 1D plot canvas. Selection: 2D main image chosen by clicking a thumbnail (then that thumbnail highlighted). 1D: only checked curves are plotted.

- **Status bar:** Single line below the tabbed area; text from `status_var`; background color from `STATUS_COLORS` in `fast2dprocess_gui/core/style.py`: default (gray), progress (lightblue/darkblue), success (green), error (red). Reset to default color after 5 seconds following an error (calibration error, working-directory validation error, or invalid file).

- **Text copying:** Right-click (Button-3 / Button-2) on labels and entries opens a “Copy” context menu; recursive attach from root via `enable_text_copying_recursive(root)` after widgets are created. Copy uses widget text or entry selection/content.

---

## 11. Style and theming

- **Location:** All visual style and the application color theme are defined in **`fast2dprocess_gui/core/style.py`**. No color theme, fonts, colors, or other style values are hardcoded in GUI or other code; they are always imported from this module (e.g. `from fast2dprocess_gui.core.style import FONTS, COLORS, STATUS_COLORS, COLOR_THEME`). Status bar colors live in this module, not in `core/constants.py`.

- **Contents of the style module:** The module exposes at least the following, matching the current app appearance:
  - **`COLOR_THEME`** — the CustomTkinter default color theme; current value `"blue"`. Applied at startup (e.g. `ctk.set_default_color_theme(COLOR_THEME)`) from the entry point; not set elsewhere.
  - **`STATUS_COLORS`** — dict keyed by status type (`"default"`, `"progress"`, `"success"`, `"error"`), values are (light, dark) tuples for CustomTkinter. Current values: default `("gray85", "gray25")`, progress `("lightblue", "darkblue")`, success `("green", "darkgreen")`, error `("red", "darkred")`.
  - **`FONTS`** — font definitions used across the GUI. Concrete values: status bar 14 pt bold; section/button titles (e.g. "Calibration Parameters", "Apply Calibration") 14 pt bold; panel titles ("Images", "Curves", "Plot Type:") 12 pt bold; drop-zone labels 12 pt; curve list checkboxes 10 pt; thumbnail labels (filename under thumb) 9 pt; plot legend 9 pt.
  - **`COLORS`** (or equivalent) — widget colors. Concrete values: thumbnail selected `("gray75", "gray35")`, unselected `("gray90", "gray20")`; drop-zone label background `"transparent"`.
  - **Plot-related constants** — 1D curve colormap name `"tab10"`; default single-curve scatter color `"#1f77b4"`; legend font size 9.
  Every style value used anywhere in the app must be defined in this module.

- **Usage rule:** Every place that sets or applies theme or appearance (CustomTkinter color theme, widgets, status bar, plot defaults) must import and use symbols from `fast2dprocess_gui.core.style`. The color theme is applied at startup from the style module (e.g. `ctk.set_default_color_theme(COLOR_THEME)` in the entry point); it must not be set from a hardcoded value elsewhere. Adding a new style dimension requires adding it to the style module first, then using the new symbol where needed.

---

## 12. 1D curves tab

- **Identification:** Curves stored in `CurvesTab1D.curves` keyed by `unique_id = os.path.abspath(str(file_path))`. Each value: (file_path, curve_type, checkbox_var, checkbox_widget, filename).

- **Plot types (exact list):** “I vs q”, “log I vs q”, “log I vs log q”, “Guinier: log I vs q^2”, “Kratky: q^2 * I vs q”. Axes/labels and data transform (e.g. log10(I), q², q²×I) are set per plot type. q displayed in nm⁻¹ (internal q in 1/m multiplied by 1e-9).

- **When plots are saved:** On add of a curve, only the main 1D plot is saved (`plot_1d_<basename>.png`) via `save_all_curve_plots(curve_path)`. When the user changes plot type with the segmented button, `save_current_plot_type()` runs and saves specialized plots only for the currently selected type (not “I vs q”): log I vs q → “logI_vs_q”, log I vs log q → “loglog”, Guinier → “guinier”, Kratky → “kratky”, each as `<type>_<basename>.png` in the working directory.

- **Default selection:** When a new curve is added, it becomes the only checked curve (all others unchecked); `last_added_curve` is set to its unique_id. Duplicate path (same unique_id): add returns without adding; no duplicate entries.

---

## 13. 2D images tab

- **Thumbnails:** Keyed by `unique_id = os.path.abspath(str(image_path))`. Value: (image_path, thumb_widget, image_type, filename). If the same path is added again, only selection is updated (no second thumbnail).

- **Main image:** Chosen by clicking a thumbnail; `_select_image(unique_id, image_path, image_type)` updates `selected_image` and calls `display_image(image_path, display_title)`; thumbnail highlight (fg_color) uses colors from `fast2dprocess_gui/core/style.py` (e.g. `COLORS["thumbnail_selected"]`, `COLORS["thumbnail_unselected"]`).

- **Image reading fallbacks:** For both thumbnail and main display: (1) `read_from_tiff(image_path)`; on exception (2) `fabio.open(image_path).data`; on exception (3) `IntegratorExtended.read_mask(image_path)` (mask converted to float for display). If all fail, error is printed and no display/thumbnail update.

- **When 2D plots are saved:** Calibrant: after calibration complete, `image_tab_2d.save_calibrant_plot(calibrant_path)` → `calibrant_2d_<basename>.png` in the working directory. Buffer/sample: after processing in worker, `image_tab_2d.save_image_plot(image_path, image_type)` → `<image_type>_2d_<basename>.png` in the working directory.

---

## 14. Threading and environment

- **Threading env (`threading_env`):** In `fast2dprocess_gui/utils/threading_env.py`. Variables: OMP_NUM_THREADS, MKL_NUM_THREADS, NUMEXPR_NUM_THREADS, OPENBLAS_NUM_THREADS, VECLIB_MAXIMUM_THREADS, BLIS_NUM_THREADS, TBB_NUM_THREADS, NUMBA_NUM_THREADS. Set to `'1'` in `setup_threading_env()` to avoid deadlocks in worker threads. Must be set before importing NumPy/SciPy/pyFAI. Set at import time of the module and explicitly in `fast2dprocess_gui.py` before other imports. Restored in `restore_threading_env()` on window close (`WM_DELETE_WINDOW`) and registered with `atexit`. Original values are stored at import and restored so closing the app restores the environment.

- **Calibration:** Runs in a separate process (calibration_service.py), not only a thread. The GUI starts a thread that runs the subprocess and waits for it; status is polled on the main thread via `root.after(500, check_status)`.

- **Other background work:** Processing (integrate, subtract) runs in daemon threads; status callbacks and GUI updates are marshalled to the main thread with `root.after_idle` or `root.after`.

---

## 15. Error handling and validation

- **Before storing a file:** Image: extension .tif/.tiff and file exists; else status error, return False from on_file_drop, labels reset. Mask: extension .npy/.txt/.msk, file loads, exactly two unique 0/1-like values; else status error, return False, labels reset.

- **Before calibration:** Calibrant must be set; required params (wavelength, detector_distance, pixel_size, beam_center_x, beam_center_y) must be set; `build_calibration_config` raises ValueError if any required missing. If calibrant missing or calibration already running, status callback and CALIBRATION_ERROR used and run_calibration returns False.

- **On calibration failure:** Subprocess writes status file with type "error"; result file may contain status "error" and "error" message. GUI sets status bar to error, stops monitoring; `_handle_calibration_error` sets calibration_running to False and publishes CALIBRATION_ERROR. No partial state: successful calibration is applied only after result file and integrator are read and copied.

- **On processing failure:** ProcessingService catches exception, updates status callback with error, publishes PROCESSING_ERROR; returns None. Main window does not add curve or run subtraction if output_path is None.

- **Working directory at launch:** The directory selection dialog must require an empty directory. If the user selects a non-empty directory: show an error (e.g. status or dialog message) and re-prompt until the user selects an empty directory or cancels. If the user cancels without selecting a valid directory, the application exits. The chosen path is stored as the working directory for the session; all writes go there.

---

## 16. Invariants and edge cases

- **Calibrant required before calibration:** Apply Calibration with no calibrant shows “No calibrant image loaded”. Calibration run in the service also checks calibrant and publishes CALIBRATION_ERROR if missing.

- **Single path per type except sample:** DataManager holds a single path per FileType for CALIBRANT, BUFFER, and MASK (last drop wins). For SAMPLE, DataManager holds a **list of paths**; dropping one or more files on the sample zone **replaces** the sample list with the set of valid image paths from that drop. Only the sample zone accepts multiple files.

- **Subtraction uses the most recently added buffer curve:** For a given sample, subtraction is performed with the buffer curve found by iterating `curves_tab_1d.curves` in reverse and taking the first entry with curve_type "buffer" and existing path. If no buffer curve exists, no subtraction is created.

- **Duplicate file path:** 2D tab: adding the same path again does not add a second thumbnail; selection is updated to that image. 1D tab: `add_curve` returns False if unique_id already in `curves`; no duplicate curve entry.

- **Calibrant dropped but calibration not run:** Buffer or sample(s) can be dropped but are not auto-processed until calibration has completed. Until then, dropping buffer or samples only stores the path(s) and, for calibrant, displays the 2D image; for buffer/samples, status may show “Please calibrate first” and 2D is not displayed unless it was already calibrated.

- **Sequential auto-processing after calibration:** When calibration completes with `buffer_path` and/or a non-empty sample list set, the application processes them sequentially in a single worker thread: buffer first (if set), then each sample in the sample list in order.

- **npt and integration options:** npt is fixed at 1000 for 2D→1D integration; not exposed in the GUI. Buffer subtraction uses method `match_tail` with default options; not exposed in the GUI.

- **Threading env:** Restored on WM_DELETE_WINDOW and atexit; subprocess sets its own env (all cores) and does not use the GUI’s threading env after launch.
