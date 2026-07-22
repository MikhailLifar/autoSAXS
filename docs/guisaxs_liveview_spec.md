# guisaxs-liveview — Technical Specification

This document specifies a new desktop GUI application (“guisaxs-liveview”) for **live, queued processing** of incoming SAXS `.tif` images in a watched directory. It is written to be precise enough to implement **while reusing `src/guisaxs_skills/` widgets and conventions as much as possible**.

---

## Table of contents (non-optional reading)

- **1. Purpose and non-goals**
- **2. Non-negotiable constraints**
- **3. User workflow (session narrative)**
- **4. Processing state machine (A/B/C plus analysis mode)**
- **5. Directory watching + queueing (stability, ordering, backpressure)**
- **6. UI layout (three columns) and required widgets**
- **7. Output directories and naming (autosaxs conventions)**
- **8. Right column: analysis modes and skill contracts**
- **9. Edge cases and invariants**

---

## 1. Purpose and non-goals

### 1.1 Purpose

**One-sentence summary:** **guisaxs-liveview** is a single-window desktop GUI that watches a directory for **new stable `.tif` files**, processes them **sequentially** via `autosaxs` skills, and continuously updates live plots (2D + 1D) and optional **right-column analysis** outputs according to a user-selected analysis mode (monodisperse analysis, polydisperse analysis, or off).

**Main user goal:** Start a session by choosing a **watch directory**, then iteratively configure processing during the session (calibration → buffer) while the app keeps up with incoming data using a **FIFO queue** and never freezes.

### 1.2 Non-goals (explicit)

- The app is **not** a general-purpose “skill console” (that is `guisaxs-skills`).
- The app is **not** responsible for implementing scientific logic in-process; it must be a thin orchestration/UI layer on top of skills.
- The app does **not** attempt to parallelize processing of images; correctness and determinism are prioritized over throughput.

---

## 2. Non-negotiable constraints

### 2.1 Reuse `guisaxs_skills` by maximum extent possible (hard requirement)

The implementation MUST reuse the existing `src/guisaxs_skills/` building blocks wherever feasible, including (non-exhaustive):

- `ui/path_field.py` for directory selection and file path inputs (DnD + browse + manual entry).
- `ui/curve_plot.py`, `ui/preview_panel.py`, and any existing viewer components for `.png`/curve previews.
- `ui/style.py` for styling and consistent look.
- `logic/runner_qprocess.py` (or the same approach) for running autosaxs skills in isolated processes.
- The existing **calibrate skill panel** patterns from `guisaxs_skills` (parameters form + Run + outputs preview).

**Interpretation:** guisaxs-liveview should be implemented as a **separate GUI entry point** inside the **same `guisaxs_skills` package**, sharing widgets, styles, and runner logic instead of duplicating them.

### 2.2 Skills-only compute backend (hard requirement)

The GUI MUST NOT call `autosaxs.processor`, pyFAI, or subtraction/integration routines directly. All processing must be performed by invoking public `autosaxs` skills (typically via CLI) in isolated processes:

- `integrate_proxy`
- `calibrate`
- `integrate`
- `subtract`
- **Analysis skills (right column; subset may run per file depending on selected mode, see §8):** `fit_guinier`, `fit_distances`, `model_dam`, `model_bodies`, `fit_sizes`, `model_mixture`

Preview-only reading of existing `.dat`/`.png` files for display is allowed.

### 2.3 Caching disabled for all skill runs (hard requirement)

All autosaxs skills invoked by this app MUST be run with caching **disabled**:

- Python API form: pass `use_cache=False`
- CLI form: pass `--no-cache`

This applies to every run of: `integrate_proxy`, `calibrate`, `integrate`, `subtract`, and every analysis skill listed in §2.2 (`fit_distances`, `model_dam`, `model_bodies`, `fit_sizes`, `model_mixture`).

### 2.4 Isolation + responsiveness (hard requirement)

- The UI thread MUST remain responsive during processing.
- Processing MUST occur outside the UI thread.
- A single sequential worker MUST be used for the incoming-image queue (FIFO).

### 2.5 Deterministic ordering (hard requirement)

- Incoming `.tif` files MUST be processed in **FIFO order by detection time**.
- A file is eligible only after it is **stable** (see §5.2).

---

## 3. User workflow (session narrative)

### 3.1 Start session

1. On launch, the app prompts the user to **select the watch directory** using the **same directory selection dialog/logic as `guisaxs_skills` working-directory selection** (i.e., a Qt directory picker with non-native dialog, detail view sizing, and validation that the directory exists and is writable).
2. After a valid directory is selected, the app starts watching it for new `.tif` files.
3. Only **new** files are processed (files already present at watcher start are ignored).

### 3.2 Switching watch directory during a session (required)

The main window MUST include a top header/panel and a menu action analogous to `guisaxs_skills`’ “Open working directory…” flow:

- **UI affordance**: provide either (or both):
  - a top header/panel action (button/link) “Change watch directory…”
  - a menu action “Open watch directory…” / “Change watch directory…”
- **Selection**: must reuse the same directory selection dialog/logic as above.
- **Reset semantics (hard requirement)**: when a new watch directory is accepted, the app MUST:
  - stop watching the old directory
  - clear the incoming `.tif` queue
  - clear all live views (middle + right)
  - reset session state to defaults (State A, analysis mode **Off**, no calibration, no buffer)
  - start watching the newly selected directory
  - apply the “only process new files” rule relative to the new watcher start time

### 3.3 Live processing evolves during the session

- At launch, the processing pipeline is minimal (State A).
- User may run calibration (transition to State B).
- User may set a buffer `.dat` (transition to State C).

The pipeline changes apply to **subsequent files** (no mandatory reprocessing of backlog; see §9 for optional future behavior).

---

## 4. Processing state machine (A/B/C plus analysis mode)

### 4.1 Definitions

- **Watch dir:** the directory being monitored; all outputs are written under it.
- **Incoming image:** a `.tif` that was detected as new and became stable.
- **Per-file pipeline:** the exact sequence of skill invocations performed for one incoming image.
- **Middle column view:** always reflects the **latest fully processed** incoming image (not “currently processing”).
- **Analysis mode:** user choice in the right column, implemented as a **drop-down list**. The **first option is always `Off`** (default): no analysis skills run after integration/subtraction. Any other option selects a concrete analysis pipeline and UI (see §6.4 and §8).

### 4.2 State A — Default (no calibration, no buffer)

**Pipeline (per incoming image):**

- Run `integrate_proxy` on the `.tif`.
- Save `.dat` outputs under `averaged_proxy/` in the watch directory (see §7).

**Middle column display (required):**

- 2D image view for the incoming `.tif`.
- 1D curve integrated in **pixel space** (proxy integration) as the main curve plot.

**Right column analysis (required behavior):**

- Analysis skills are **never run** in State A regardless of the drop-down selection.
- The user MUST be allowed to **choose any analysis mode (including non-`Off`) before calibration**; the choice is **remembered** and applies automatically to **subsequent** files once State **B** or **C** is active (no need to re-select after calibrating).
- While still in State A, the UI SHOULD show a clear note that analysis runs only after calibration (e.g. “Analysis runs after calibration”), without blocking or clearing the user’s mode choice.

### 4.3 State B — Calibrated (calibration set, no buffer)

**Entry condition:** calibration run succeeded and produced a valid integrator directory usable by `integrate`.

**Pipeline (per incoming image):**

- Run `integrate` (using the calibrated integrator directory from the successful `calibrate` run).
- Save `.dat` outputs under `averaged/` in the watch directory.
- If analysis mode is **not** `Off`, append the skill sequence for that mode (§8) on the **latest integrated q-space curve** for this file.

**Middle column display (required):**

- 2D image view for the incoming `.tif`.
- 1D curve in **q-space** \((q\ \mathrm{nm}^{-1})\) as the main curve plot.

**Right column analysis (required behavior):**

- If analysis mode is `Off` (State **B**): show the mode selector and an idle / no-analysis state; do not run analysis skills.
- If analysis mode is not `Off` (State **BD**): run the selected mode’s skills on the integrated curve (§8) and update the right column with that mode’s plots and viewers (fit comparison and mode-specific outputs).

### 4.4 State C — Calibrated + buffer set

**Entry condition:** State B plus a buffer `.dat` file has been selected by the user.

**Pipeline (per incoming image):**

- Run `integrate` to produce the sample curve in q-space (saved under `averaged/`).
- Run `subtract` using:
  - sample = the newly integrated curve
  - buffer = the selected buffer curve
- Save subtracted outputs under `subtracted/` in the watch directory.
- If analysis mode is **not** `Off`, append the skill sequence for that mode (§8) on the **latest subtracted curve** for this file.

**Middle column display (required):**

- 2D image view for the incoming `.tif`.
- No integrated q-space curve plot as in State B.
- Two graphs at the **bottom** of the middle column instead:
  1. **Left bottom:** sample curve + **scaled buffer curve used for subtraction**, plotted as **log \(I\) vs \(q\)**.
  2. **Right bottom:** subtracted curve, plotted as **log \(I\) vs \(q\)**.

**Right column analysis (required behavior):**

- If analysis mode is `Off` (State **C**): same as State **B** with `Off` — no analysis skills; idle state.
- If analysis mode is not `Off` (State **CD**): run the selected mode’s skills on the **subtracted** curve (§8) and update the right column accordingly.

### 4.5 Analysis mode vs calibrated states (BD / CD)

In calibrated states, whether analysis runs is determined **only** by the drop-down:

- **`Off`:** no analysis skills after `integrate` (State **B**) or after `integrate` + `subtract` (State **C**).
- **Not `Off`:** analysis runs according to §8 on the same 1D input as above (**BD:** integrated q-space curve; **CD:** subtracted curve).

**Invariant:** Analysis skills are never run in State A.

**Default:** On app launch and after **change watch directory** (§3.2), analysis mode MUST reset to **`Off`**.

---

## 5. Directory watching + queueing (stability, ordering, backpressure)

### 5.1 Watch rules

- Watch only for files with extension `.tif` (case-insensitive).
- Process only **new** files detected after the watcher is started.

### 5.2 Stability rule (hard requirement)

Before enqueueing or processing, the app MUST ensure the `.tif` file is **fully written**. The stability heuristic MUST be explicitly implemented, e.g.:

- consider a file stable if its **size and mtime** remain unchanged across \(N\) consecutive checks (e.g. 2–3 checks) with a small delay (e.g. 200–500 ms).

If the file never becomes stable within a configured timeout, it is skipped with an error message and the queue continues.

### 5.3 Queue semantics (hard requirement)

- Use a FIFO queue ordered by **detection time**.
- Use a single worker that processes items strictly sequentially.
- The queue may grow large (e.g. 1000 items). This must not crash the app.

### 5.4 UI status requirements for queue

The UI MUST show (a small button at the bottom of the middle column, opening a live wizard):

- current queue size
- currently processing filename
- last processed filename
- average processing time (best-effort) and/or estimated time remaining (optional but recommended)

### 5.5 Failure policy

- If processing of a file fails: **skip it**, record/log the error, and continue to the next file.
- Failures are expected to be rare; nevertheless the UI must make failures discoverable (log panel + a short status banner/toast).

---

## 6. UI layout (three columns) and required widgets

### 6.1 Single-window layout

Use a three-column main window (a horizontal splitter) consistent with `guisaxs_skills` UI:

- **Left:** parameters
- **Middle:** live view
- **Right:** analysis (mode selector + mode-specific plots/viewers)

### 6.2 Left column — parameters (required sections)

#### 6.2.1 Watch directory

- The watch directory MUST be selected using the same dialog/logic as `guisaxs_skills` working-directory selection (see §3.1).
- After selection, show the chosen watch directory as a **read-only, selectable text** label in the main window header area (matching the `Workdir: ...` label pattern in `guisaxs_skills`).
- Start/Stop watching controls (or Watch toggle), plus current status indicator.

#### 6.2.2 Calibration panel (top)

- Must reuse the existing “calibrate skill panel” patterns from `guisaxs_skills`.
- User sets parameters for `calibrate` (matching the existing calibrate panel in `src/guisaxs_skills/`).
- On “Run”:
  - invoke `calibrate` (isolated process)
  - on success: transition to State B
  - show a **small preview** of the calibrated curve output within the panel
  - clicking the preview opens the standard `.png` viewer from `guisaxs_skills`
 - Calibration results MUST be written under a dedicated `calibration/` subdirectory in the watch directory (see §7.1).

#### 6.2.3 Buffer panel (bottom)

- Must reuse the existing `subtract` skill panel patterns from `guisaxs_skills`:
  - a file picker for a buffer `.dat`
  - subtract parameters fields (matching the `subtract` skill panel)
  - optional advanced section (if the existing panel has one)
- After buffer selection:
  - show a small preview (curve) within the panel
  - clicking opens the standard viewer
  - transition to State C (if calibration is already successful)

**Constraint:** If the buffer is selected before calibration, the UI may store it but State C activation still requires calibration; subtraction is not run until calibrated integration is available.

**Persistence requirement:** the chosen buffer `.dat` and the subtraction parameter config (YAML `.conf`) MUST be written to `subtracted/` in the watch directory (see §7.1).

### 6.3 Middle column — live view

The middle column is the “live dashboard” that updates after each fully processed file:

- 2D image view (latest processed `.tif`)
- In State B: 1D main curve plot 
- In State C: Two bottom plots as specified in §4.4
- A queue view thin button
- A log/status area is allowed, but the middle column must prioritize live visuals.

### 6.4 Right column — analysis view

The right column is driven by an **analysis mode** drop-down (**`Off` first**, default **`Off`**). Each option defines which skills run (when calibration allows; §4) and which widgets are shown. The column SHOULD reuse `guisaxs_skills`-style parameter panels and plots where equivalents already exist.

**Drop-down options (fixed order, exact user-visible labels):**

1. **`Off`** — no analysis skills; idle / placeholder when uncalibrated; when calibrated, no post-integration analysis.
2. **`Monodisperse analysis`** — launches a **separate wizard window** (3-pane: Guinier → GNOM → optional shape); auto pipeline runs `fit_guinier` then `fit_distances`; shape (`model_bodies` / `model_dam`) on demand only via **Re-run shape** (default shape mode **None**).
3. **`Polydisperse analysis`** — launches a **separate analysis window** (3-pane: Guinier → fit_sizes / D(R) → optional model_mixture); auto pipeline runs `fit_guinier` then `fit_sizes` (spheres, `first` default 1); mixture on demand when enabled (default **None**). Guinier is display-only and **not** handed off to fit_sizes.

**Common requirements:**

- **Monodisperse wizard:** separate large dialog (not embedded in the right column); plots from structured files (`.dat`, GNOM `.out`, `.fir`, `.cif`) only; wizard control changes **suspend** the TIFF queue until **Resume auto-processing** (enabled when idle).
- **Latest analysis status** (Idle/Running/Failed) for the active mode.
- **Changing the selected mode** affects **only subsequent** incoming files after the change (§9.1); the UI MAY update immediately to the new layout, but MUST NOT re-run skills for already processed files.

---

## 7. Output directories and naming (autosaxs conventions)

### 7.1 Output directories (hard requirement)

Under the watch directory, the app MUST create (if missing):

- `calibration/` — outputs from calibration runs (integrator dir, refined config, calibration plots)
- `averaged_proxy/` — outputs from State A
- `averaged/` — integrated q-space outputs from State B/C
- `subtracted/` — outputs from State C
- `guinier/` — per-sample Guinier fits (`guinier/<stem>/`; watchdir-level `guinier/guinier.conf` for wizard interval)
- `fit_distances/` — per-sample GNOM / \(p(r)\) outputs (`fit_distances/<stem>/`)
- `model_bodies/` — BODIES shape fits (`model_bodies/<stem>/`; optional when monodisperse shape mode is BODIES)
- `dammif/` — DAMMIF shape fits (`dammif/<stem>/`; optional when monodisperse shape mode is DAMMIF)
- `runs/` — per-run logs/stdout/stderr/result dicts for traceability, consistent with `guisaxs_skills` conventions

**Additional persistence requirement (hard):**

- `subtracted/` MUST also contain:
  - the selected buffer `.dat` (copied or referenced per autosaxs conventions)
  - the subtraction parameters config file in YAML format (extension `.conf`)

### 7.2 Naming policy (hard requirement)

File naming MUST follow the standard autosaxs conventions already used elsewhere in this repository (see `src/guisaxs_skills/` practices). The liveview app must **not** invent a new naming scheme.

The spec intentionally does not restate exact filename templates here; the implementation must centralize naming using the same helper(s)/conventions as `guisaxs_skills` (and/or autosaxs utilities) so outputs are consistent across tools.

---

## 8. Right column: analysis modes and skill contracts

### 8.1 When to run (input curve)

For **every** analysis mode except **`Off`**, the **same input rules** apply:

- State A: **never** run analysis skills.
- State **BD** (calibrated, no buffer, mode ≠ `Off`): run on the **latest integrated q-space** `.dat` for the current file.
- State **CD** (calibrated + buffer, mode ≠ `Off`): run on the **latest subtracted** `.dat` for the current file.

No mode in this spec uses a different input source than the above.

### 8.2 Black-box rule

The GUI MUST treat each skill as a **black box**: display artifacts, logs, and plots produced by the skill (and standard autosaxs run metadata). It MUST NOT reimplement scientific logic in-process.

Cross-pane parameter handoff in the monodisperse wizard (Guinier interval → GNOM, GNOM → shape) is **orchestration**: the GUI reads YAML/metadata from prior skill outputs and passes explicit options into subsequent skill invocations. All fits still run via skills; the wizard does not perform Guinier/GNOM/BODIES/DAMMIF math in-process.

### 8.3 Per-mode skill sequence and UI mapping

| Drop-down label | Skill(s) (in order) | Right-column content (minimum) |
|-----------------|---------------------|--------------------------------|
| `Off` | *(none)* | Mode selector + idle / placeholder |
| `Monodisperse analysis` | `fit_guinier` → `fit_distances` (auto); optional `model_bodies` / `model_dam` (manual) | Separate wizard window: Guinier, GNOM, shape (None/BODIES/DAMMIF) |
| `Polydisperse analysis` | `fit_guinier` → `fit_sizes` (auto); optional `model_mixture` (when enabled) | Separate analysis window: Guinier (independent), fit_sizes / D(R), optional mixture |

**Monodisperse shape chaining:** When the user selects **BODIES** or **DAMMIF** and presses **Re-run shape**, `model_dam` MUST consume the **GNOM result** from the latest `fit_distances` run (`best_gnom_out_path`). `model_bodies` uses the profile curve plus Guinier/GNOM handoff parameters. Shape skills do **not** run automatically in the TIFF pipeline (default shape mode is **None**).

**Polydisperse chaining:** Guinier pane edits re-run **only** `fit_guinier` (no handoff into `fit_sizes`). `fit_sizes` always uses `shape=spheres` and an explicit `first` (default **1**). When mixture mode is **Mixture**, auto TIFF jobs append `model_mixture`; enabling mixture mid-run may enqueue a mixture-only follow-up after a successful `fit_sizes`. Mixture `r_max` / `poly_max` start unset (skill-derived) and the pane controls update from the resolved values after a run (same pattern as fit_sizes `last`). Window panes use **data-driven matplotlib viewers** (`.dat`, GNOM `.out`, `dr_csv`, MIXTURE `.fit` / CSV) — not PNG thumbnails.

**Monodisperse queue suspension:** Any wizard control change (Guinier interval, GNOM parameters, shape mode, body checklist) MUST **pause** the FIFO queue, **cancel** the running skill (requeue current job), and allow unlimited re-processing of the **current curve** via manual jobs. Incoming TIFFs remain queued but are not processed until the user presses **Resume auto-processing** (enabled only when no skill is running). This mirrors subtraction-wizard intervention semantics but stays embedded (explicit resume required).

### 8.4 Performance and queueing

Analysis skills may be slower than integration/subtraction. The implementation MUST choose one of:

- **Option 1 (simple, recommended):** run the selected mode’s skill sequence **inline** as part of the **per-file sequential** pipeline, so the queue worker does not start the next incoming image until the current file’s analysis (if any) has finished.
- **Option 2 (advanced):** maintain a separate sequential analysis queue that always processes the **latest available** curve (dropping intermediate analysis tasks) while integration/subtraction continues.

**This spec requires Option 1** for all modes for determinism. Users who need faster queue throughput SHOULD set analysis mode to **`Off`**. Option 2 remains a future option if performance requirements change.

### 8.5 Failure policy within a mode

For multi-step monodisperse auto-processing (`fit_guinier` → `fit_distances`), failure of an earlier step implies later steps are not run for that file. Shape fits are manual and do not block the auto queue. Global per-file failure handling remains §5.5 (skip file, log, continue queue).

### 8.6 `model_bodies` — body-model subset (skill contract)

The `model_bodies` skill MUST accept an optional argument specifying which ATSAS **body** models to fit:

- **Default:** `None` (or equivalent) means **all** supported models (the full canonical set used by the skill).
- **Non-default:** a **subset** of model names (any non-empty subset of that canonical set). The monodisperse wizard **BODIES** shape pane MUST expose this as user-configurable parameters and pass them into the skill invocation.

Canonical names are defined in code (`BODIES_SHAPES_LIST` in `src/autosaxs/skill/model_bodies.py`); the UI SHOULD list the same names for multi-select.

### 8.7 Interactive 3D viewer (implementation requirement)

The monodisperse wizard **shape** pane (BODIES / DAMMIF) requires an **interactive 3D** view: the user can **rotate** the model and view it from **different angles**. The **same** 3D viewer component MUST be reused for **both** DAMMIF (`.cif`) and BODIES (analytical isosurface) so behavior and maintenance stay consistent. It MUST be distinct from existing **2D** curve / image viewers in `guisaxs_skills` (those remain valid for \(I(q)\), \(p(r)\), etc.). Wizard plots are rendered from structured artifacts (`.dat`, GNOM `.out`, `.fir`) only — not from skill-emitted PNG thumbnails.

---

## 9. Edge cases and invariants

### 9.1 No reprocessing by default

Changing **calibration**, **buffer**, or **analysis mode** (drop-down) during a session affects only **future** incoming files. The app does not automatically reprocess previously processed files or the current queue backlog.

(A future enhancement may add explicit “Reprocess backlog from X” controls; not required.)

### 9.2 Burst handling

If 1000 files arrive in 10 seconds and each requires ~1 second processing:

- the queue size may peak near 1000
- the app must continue processing sequentially until the queue empties
- UI must remain responsive and show queue progress

### 9.3 Duplicate/overwrite considerations

If autosaxs skills overwrite outputs for identical stems, the liveview app must not attempt to invent its own anti-overwrite logic. It should rely on autosaxs caching/behavior and ensure the UI always points to the latest produced artifacts.

### 9.4 Crash safety

If the app is closed mid-queue, no special recovery is required; on restart, previously existing `.tif` files are ignored because only “new files after watcher start” are processed.

