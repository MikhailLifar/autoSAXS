# guisaxs-skills — Technical Specification

This document specifies a new desktop GUI application (“guisaxs-skills”) that is a **strict interface to `autosaxs` skills**. It is written to be precise enough to implement without re-introducing workflow logic that bypasses skills.

---

## Table of contents (non-optional reading)

- **1. Purpose and usage cases**: defines what the app *is* (a skill console, not a pipeline GUI).
- **2. Non-negotiable constraints**: **during development YOU MUST NOT violate these** (skills-only API, isolation, one-window, working dir policy).
- **3. Technological stack**: implementation choices that keep the UI responsive and portable.
- **4. Architecture (layered)**: strict separation of interaction vs logic, connected **only via EventBus**.
- **5. Development principles**: **during development YOU MUST obey these** (module size discipline, no hidden behavior, determinism/traceability, no UI freezes, safety).
- **6. Skill execution model (process isolation)**: how runs are launched, monitored, parsed, and cancelled.
- **7. UX (general)**: session start, running skills, artifact inspection, one-click access.
- **8. Layout A (general)**: the single-window three-column layout with persistent splitters.
- **9. UX and middle-column data per skill (required)**: what the middle column must show for each skill.
- **10. Layout per skill (required)**: layout invariants and per-skill emphasis.
- **11. Working directory policy (detailed)**: required on-disk structure and invariants.
- **12. Edge cases and invariants**: behavior guarantees and failure modes.

---

## 1. Purpose and usage cases

**Purpose (1 paragraph):** guisaxs-skills is a single-window PyQt5 desktop application that lets a user **discover, configure, run, and inspect `autosaxs` skills** (calibrate/integrate/subtract/plot/guinier/fit/…) while keeping all artifacts inside a user-selected **working directory**. The app is not a “pipeline GUI”; instead it is a **skill console** that can run any skill in one click, show progress/logs without freezing, and present the resulting output paths and plots produced by the skill.

**Primary usage cases:**
- **Manual skill execution**: run one skill at a time with explicit inputs/kwargs, inspect outputs.
- **Batch operations**: use directory/glob inputs supported by skills (expansion rules apply).
---

## 2. Non-negotiable constraints

### 2.1 Skills-only API (hard requirement)

- The GUI **MUST NOT** call `autosaxs.processor` / pyFAI integration / subtraction routines directly.
- The only allowed compute operations are **invocations of public skill entry points** from `autosaxs/skill.py`:
  - `calibrate`, `integrate`, `integrate_proxy`, `subtract`, `plot`, `guinier_analysis`,
    `model_mixture`, `model_bodies`, `model_dam` (and any future public skill).
- Any legacy “convenience” functionality that reproduces part of a skill inside the GUI is forbidden.

### 2.2 Isolation (hard requirement)

- Each skill run **MUST execute in an isolated OS process**.
- The UI thread **MUST remain responsive** during all runs.
- Runs must be cancellable (terminate the process tree; see §6.5).

### 2.3 One-window principle (hard requirement)

- The application is **single-window**. Auxiliary dialogs are allowed only for:
  - choosing/creating the working directory at launch
  - choosing files/dirs for inputs
  - showing non-blocking “About/Help” content
- All skills must be reachable in **one click** from the main window’s skill list.

### 2.4 Working directory at launch (hard requirement)

- On launch, the user must select or create a **working directory** (it may be non-empty).
- The app does not proceed until a valid directory is chosen; cancel exits.
- All outputs are stored under this working directory (either directly under it or under skill-specific subdirectories).
- The working directory is immutable for the session (changing it requires restart).

**Overwrite/cache policy (required):**
- The GUI does not attempt to “protect” users from overwrites by enforcing an empty directory.
- Behavior is determined by the invoked skill and the caching flag:
  - with `use_cache=True`, cache hits should result in **no recomputation** (and typically no output modification)
  - with `use_cache=False`, outputs may be **overwritten** by the skill

---

## 3. Technological stack

- **UI framework:** PyQt5 (widgets + model/view + `QSplitter` layout).
- **Process execution:** `QProcess` (preferred) for asynchronous run/cancel and incremental stdout/stderr capture.
- **Plot display:** Qt-embedded Matplotlib (`FigureCanvasQTAgg`) for PNG/SVG viewing and/or direct matplotlib embedding.
- **Data previews:** simple file viewers:
  - images: `.tif/.tiff` shown via rendered PNGs produced by skills (preview-only; no processing)
  - curves: `.dat` read for preview using `autosaxs.utils.read_saxs` in a **preview-only** mode (must not compute derived outputs)
- **Autosaxs interface:** execute skills via the CLI dispatcher (`python -m autosaxs.cli <skill> ...`), or via a dedicated “runner” module that still spawns a process and imports `autosaxs.skill` inside that process (see §6.2).

---

## 4. Architecture (layered)

The app strictly separates **interaction** from **logic**; the only connection is an internal **EventBus** (similar to the existing pattern in `autosaxs` / `guisaxs`).

### 4.1 Layers

- **Interaction layer (UI)**
  - Owns widgets, layout, drag-and-drop, and local presentation state (selected skill, selected artifacts, etc.).
  - Publishes high-level intent events (e.g. “run skill X with these arguments”).
  - Subscribes to progress/log/result events and updates the UI.
  - **Must not** implement any skill logic or call autosaxs compute APIs.

- **Logic layer (skill runner + working-dir manager)**
  - Owns:
    - working directory validation and session state
    - serialization of skill invocations
    - launching/stopping isolated processes
    - parsing results into structured “run artifacts”
  - Subscribes to UI requests and publishes progress/log/result events.
  - **Must not** manipulate widgets directly.

- **Shared core**
  - Event definitions, data models (`SkillSpec`, `RunRequest`, `RunState`, `Artifact`), constants, and utilities (path copying, safe quoting, etc.).

### 4.2 EventBus contract (only coupling)

The following event types are required (names illustrative; implement as enums/strings consistently):

- **UI → Logic**
  - `WORKDIR_SELECTED(path)`
  - `RUN_SKILL_REQUESTED(request: RunRequest)`
  - `CANCEL_RUN_REQUESTED()` (cancels the current run)
  - `OPEN_ARTIFACT_REQUESTED(path)`
  - `COPY_CLI_REQUESTED(request)`

- **Logic → UI**
  - `STATUS(text, level)`
  - `RUN_STARTED(skill_name, started_at)`
  - `RUN_STDOUT(text)`
  - `RUN_STDERR(text)`
  - `RUN_PROGRESS(fraction|None, label|None)` (best-effort)
  - `RUN_RESULT(result: dict)` (parsed return dict from skill)
  - `RUN_FAILED(error_summary, diagnostics)`
  - `RUN_CANCELLED()`
  - `ARTIFACTS_UPDATED(artifacts: list[Artifact])`

**Important:** the EventBus is internal to the GUI app. Autosaxs’ own `EventBus` inside a skill is not shared with the GUI; any “messages” emitted by skills are surfaced via captured stderr/stdout (see §6.3).

---

## 5. Development principles

### 5.1 Single-file size discipline

- Prefer **small modules**.
- **Rule:** no single `.py` file should exceed **500 lines** (excluding type stubs and generated UI files).
- Enforce by splitting into `ui/`, `logic/`, `core/`, `models/`.

### 5.2 Rich file input fields (hard requirement)

Every **path input field** in the skill runner form (files, directories, masks, integrator dirs, config paths, and any “main path argument” that may be file/dir/glob) MUST support all of:

- **Drag-and-drop**: drop file(s) or a directory from the file manager.
- **Browse**: open a selection dialog (file picker or directory picker depending on the parameter).
- **Manual entry**: a text box where the user can type/paste a path, **directory**, or **glob expression** (including `**`).

The GUI must pass the user-provided string to the skill runner without inventing its own expansion semantics (expansion is performed by the skill/CLI behavior), while still allowing an optional “Preview matched files” feature that shows what will be processed.

### 5.3 Skills parity and no hidden behavior

- The GUI must remain a thin layer over skills:
  - Every run must be representable as a single CLI command `autosaxs <skill> ...`.
  - Provide a “Copy CLI command” action for each run.
  - Do not add GUI-only parameters that have no counterpart in the skill signature (except purely UI preferences like plot theme).

### 5.4 Determinism and traceability

- Every run creates a **run record** in the working directory:
  - `runs/<timestamp>_<skill>/request.json` (inputs, kwargs, app version)
  - `runs/<...>/stdout.log`, `stderr.log`
  - `runs/<...>/result.json` (parsed output dict)
- If a skill uses caching (`use_cache=True` default), the GUI must display whether the run was likely cached (best-effort; see §6.3).

### 5.5 No UI freezes

- No blocking I/O or computation on the UI thread.
- Use signals/slots to marshal updates back to the UI thread.

### 5.6 Safety around destructive actions

- The app must never delete user data outside the working directory.
- Cancellation must terminate only the started run’s process tree.

---

## 6. Skill execution model (process isolation)

### 6.1 Canonical invocation form

Each run is defined by:
- `skill_name` (string; must match a public function in `autosaxs.skill`)
- positional path arguments (as required by the skill)
- keyword arguments (must match the skill signature)
- `output_dir` (defaults to the session working directory, but may be a subdir)
- `use_cache` (defaults True; UI exposes “Disable cache”)

### 6.2 Runner implementation (required behavior)

**Preferred approach:** use `QProcess` to execute the autosaxs CLI dispatcher:

```
<python> -m autosaxs.cli <skill_name> <positional_args...> --output-dir <dir> [--no-cache] [--kw value...]
```

Constraints:
- `<python>` must be the same interpreter environment used for the app.
- Arguments must be passed as an array (no shell), preserving spaces safely.
- `stdout` and `stderr` must be captured incrementally and streamed into the UI log.

Alternative approach (allowed if still isolated): a dedicated runner module (e.g. `python -m guisaxs_skills.runner ...`) that imports `autosaxs.skill` and executes the function, but **still runs in a separate process**.

### 6.3 Progress and messages

- The GUI must show:
  - a run state (Idle / Running / Cancelling / Done / Failed)
  - a streaming log view (stderr + stdout tabs or merged with coloring)
- Progress/status “MESSAGE” texts emitted by skills may appear on **stdout or stderr** depending on the current autosaxs implementation.
  - The GUI must treat **both** streams as potentially containing progress.
  - Only actual errors/debug output should be assumed to belong on **stderr**.
- Optional: detect “from_cache” if the skill includes it in outputs; otherwise show cache as “unknown” unless explicitly indicated.

### 6.4 Output parsing

The autosaxs CLI prints key/value lines like:
- `key=/path/to/output`
- `key=['/p1', '/p2']` (string representation possible)

The GUI must parse outputs robustly into a dict:
- Prefer `key=value` parsing per line.
- If values are Python-literals (`[...]`, `{...}`), parse safely (e.g. `ast.literal_eval`) with fallback to raw string.
- Persist the parsed dict to `result.json`.

### 6.5 Cancellation

- “Cancel” must:
  - request termination (`terminate()`) and if not stopped within a timeout, force kill (`kill()`).
  - ensure child processes are terminated as well (platform-aware; on Linux consider process groups).
- UI must reflect cancellation and keep the app responsive.

---

## 7. UX (general)

### 7.1 Session start

- On startup: “Select or create working directory”.
- After acceptance: main window opens with:
  - working directory shown in the header (read-only) + “Open folder” action
  - skill list focused, ready for selection

### 7.2 Running a skill

- Selecting a skill instantly populates the middle column with:
  - the skill name (header) and help (full docstring in a dialog)
  - required positional inputs (clearly marked)
  - optional keyword parameters (collapsed “Advanced”)
  - `output_dir` (defaults to working dir or recommended subdir)
  - `use_cache` toggle
  - a per-skill data panel area (reserved even before the first run)
  - Run / Cancel buttons (in the run control strip, below the per-skill panel)

### 7.3 Artifacts and inspection

- After a run finishes, the right column shows:
  - “Artifacts” tree (roles → paths)
  - previews (images/plots) driven by known roles and file types
  - quick actions: open file, reveal in folder, copy path, copy CLI

### 7.4 One-click access

- The skill catalog must always be visible; switching skill is one click.
- Runs may be serialized (one active run at a time by default) to reduce confusion; allowing multiple concurrent runs is optional but must keep isolation and keep logs separated.

---

## 8. Layout A (general)

Single main window using a horizontal `QSplitter` with three columns:

1. **Left (Skill catalog)**
   - A **tab catalog (page-like)**, not a search box.
   - Tabs are grouped by category (recommended): **2D**, **1D**, **Analysis**, **Modeling**, **Utilities**.
   - Each tab shows a compact “catalog page” of skills in that category (e.g. cards or a list), each with:
     - skill name
     - 1-line purpose (first line of docstring)
     - a single-click selection behavior (selecting updates the middle/right panels)

2. **Middle (Skill runner)**
   The middle column ordering is **strict** (top-to-bottom), and must not be rearranged:

   - **Skill header**: **name only** + a small circular **“?”** help button.
   - **Input form**: positional args + keyword args (with “Advanced” section).
   - **Per-skill data panel** (see §9): the most relevant visuals/summary for the currently selected skill/run.
   - **Run control strip**: Run / Cancel, state, elapsed time, and “Copy CLI” (if present).
   - **Live log**: stdout/stderr tabs (or merged view), always last.

3. **Right (Working dir + artifacts)**
   - Working directory file tree (read-only view)
   - “Run artifacts” panel for the selected run (role → path)
   - Preview panel (image/plot/table) for selected artifact

The splitter positions must be user-adjustable and persisted between sessions (`QSettings`).

---

## 9. UX and middle-column data per skill (required)

This section defines what the middle column’s **per-skill data panel** must show *in addition to* the generic form/log.

### 9.1 `calibrate`

**Inputs:** calibration 2D image (`calib_image`), config path, optional mask + mask_mode, calibrant name.  
**Middle shows (after run):**
- **2D preview** of the calibration image (from input; preview-only)
- **Calibration curve preview** (use the output role `calibration_curve_plot_path` if present)
- **Mask visualization preview** (use `calibration_mask_path` if present)
- **Key outputs summary**: `integrator_dir`, `refined_path`, `calibration_plots_dir`
- **Next-step CTA:** “Use as integrator for `integrate`” (prefills integrator_dir into integrate form)

### 9.2 `integrate`

**Inputs:** one or many 2D images (file/dir/glob), `integrator_dir`, output_dir, options.  
**Middle shows (after run):**
- **2D preview** of the last processed image (or selected from a dropdown of inputs)
- **Integrated curve preview** for the last produced `.dat` (plot \(I(q)\) vs \(q\))
- **Batch summary table**: input → output path mapping (if list outputs are returned)
- **Next-step CTA:** “Subtract…” (prefill latest integrated_1d into `subtract` sample input)

### 9.3 `integrate_proxy`

**Inputs:** one or many 2D images, config options for proxy integration.  
**Middle shows (after run):**
- **2D preview** of image + highlighted estimated center (if a center plot is produced by the skill)
- **Proxy curve preview** for the last output
- **Quality hints** (text-only): center estimate, warnings if any were printed

### 9.4 `subtract`

**Inputs:** sample 1D (`sample_1d`), buffer 1D (`buffer_1d`), method/kwargs.  
**Middle shows (after run):**
- Plot overlay of **sample**, **scaled buffer**, and **subtracted** curve (if the skill outputs only subtracted, GUI may load inputs and output for overlay)
- Display the chosen method and q-window parameters
- Show subtracted output path role(s)

### 9.5 `plot`

**Inputs:** any supported path(s) (file/dir/glob), plot options.  
**Middle shows (after run):**
- Gallery of produced plot images (thumbnails) with click-to-preview
- For batch: list of input → plot outputs

### 9.6 `guinier_analysis`

**Inputs:** one or many 1D curves.  
**Middle shows (after run):**
- Table of extracted Guinier parameters (e.g. \(R_g\), \(I_0\), fit region) if produced
- Preview of Guinier plot image(s) (if produced)
- Link to any written report/CSV outputs

### 9.7 `model_mixture`

**Inputs:** one or many curves; mixture model options.  
**Middle shows (after run):**
- Best-fit summary (BIC/chi2, selected components) if produced
- Preview of fit plot(s)
- Primary outputs (parameter files, plots, chosen model IDs)

### 9.8 `model_bodies`

**Middle shows (after run):**
- Candidate bodies ranking (chi2) if produced
- Preview plot(s) and selected body info

### 9.9 `model_dam`

**Middle shows (after run):**
- Run status + key produced models/artifacts (e.g. output directories, `best.cif`, frequency map when `n_runs>1`)
- Any computed descriptors table (if produced)
- Warning banner: “may be long-running; cancellation will stop external ATSAS processes”

---

## 10. Layout per skill (required)

For all skills, the layout is consistent:

- **Left:** select skill.
- **Middle:** inputs form (top), run controls (middle), logs (bottom), plus the per-skill data panel (dockable area below/next to logs depending on window width).
- **Right:** artifacts tree + preview.

Skill-specific layout notes:
- **2D-first skills** (`calibrate`, `integrate`, `integrate_proxy`): per-skill data panel prioritizes 2D preview + a single representative 1D plot.
- **1D-only skills** (`subtract`, `guinier_analysis`, most fits): per-skill data panel prioritizes curve overlays and summary tables.

---

## 11. Working directory policy (detailed)

- The working directory contains:
  - `runs/` (one subdir per run; request/result/logs)
  - `inputs/` (optional copies of user-selected inputs; recommended for traceability)
  - skill outputs written to either:
    - the working directory root, or
    - a subdir chosen by the skill runner (recommended default: `<workdir>/<skill_name>/` or `<workdir>/runs/<run_id>/outputs/`)
- The UI must show “This session writes ONLY to: <workdir>”.
- Opening a non-empty directory is allowed.

---

## 12. Edge cases and invariants

- **No bypass:** if a requested action cannot be expressed as an autosaxs skill call, it must not exist in the UI.
- **Path expansion:** if a skill accepts directory/glob inputs, the GUI must not invent its own recursion rules; it should delegate to the skill/CLI behavior and display the resolved list when available.
- **Robustness:** if a skill returns paths that do not exist, the GUI must mark the artifact as “missing” and keep logs accessible.
- **Multiple outputs:** for list-valued outputs, the artifacts view must handle lists cleanly.
- **Long runs:** UI remains responsive; logs stream continuously; cancellation is always available.

