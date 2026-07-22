# guisaxs-skills — Design notes (implementation choices)

This document records **design choices** for the future implementation of the PyQt5 skills GUI described in `docs/guisaxs_skills_spec.md`. It is intentionally broader and more pragmatic than the spec: it answers “how will we structure and ship it?”

---

## 1. Repository / package structure

### 1.1 New top-level package

Create a new Python package under `autosaxs/` (package repo):

- `src/guisaxs_skills/`
  - `__init__.py`
  - `__main__.py` (enables `python -m guisaxs_skills`)
  - `app.py` (Qt application bootstrap; creates EventBus, wires layers, starts window)
  - `core/`
    - `event_bus.py` (internal app bus; UI ↔ logic only)
    - `events.py` (event names + payload dataclasses)
    - `models.py` (RunRequest, Artifact, SkillMeta)
    - `settings.py` (QSettings keys, defaults, serialization)
    - `paths.py` (working-dir layout helpers: `runs/`, `inputs/`, etc.)
  - `logic/`
    - `workdir.py` (select/validate dir; optional “create new” helper)
    - `skill_catalog.py` (discover skills from `autosaxs.skill` + docstrings)
    - `runner_qprocess.py` (QProcess runner: start/cancel/stream/parse)
    - `result_parser.py` (robust parsing of `key=value` output into JSON-safe dict)
    - `session_state.py` (current working dir, current run state, selected artifacts)
    - `inputs_copy.py` (optional: copy inputs into `inputs/` when checkbox is set)
  - `ui/`
    - `main_window.py` (single window; creates left/middle/right columns)
    - `catalog_tabs.py` (tabbed skill catalog pages)
    - `skill_form.py` (dynamic form: positional + kw-only; rich path fields)
    - `path_field.py` (DnD + browse + manual entry + “preview matches”)
    - `data_panel.py` (per-skill panel host; switches widgets by skill name)
    - `run_controls.py` (Run/Cancel/elapsed/Copy CLI)
    - `log_view.py` (stdout/stderr tabs, coloring, filtering)
    - `artifacts_panel.py` (role→path tree + actions)
    - `preview_panel.py` (image/plot/table preview)
    - `style.py` (Qt palette, fonts, spacing; single source of truth)
  - `assets/` (optional icons, including the small “?” help icon if not using a standard glyph)

**Why a separate package:** it prevents accidental reuse of legacy `guisaxs` logic and makes the “skills-only” boundary explicit.

### 1.2 No large files rule

Enforce the spec’s “<500 lines per `.py`” by splitting UI widgets and logic modules as above.

---

## 2. Installation & entry point

### 2.1 Install as an extra: `autosaxs[gui]`

The GUI should be installed via an optional dependency group so headless environments don’t pull Qt:

- **Extra name:** `gui`
- **Install:** `pip install "autosaxs[gui]"`

The extra includes (at minimum):
- `PyQt5`
- matplotlib Qt backend requirements (if not already satisfied by matplotlib install)

### 2.2 Provide a dedicated command

Add a console entry point similar to `guisaxs`:

- **Command:** `guisaxs-skills`
- **Behavior:** launches the single-window app; first step is working-dir selection.

Also support:
- `python -m guisaxs_skills` (for developer convenience)

---

## 3. Skill discovery and metadata

### 3.1 Discover skills from `autosaxs.skill`

At runtime, the catalog is built from public functions in `autosaxs.skill`:
- ignore private names (`_...`)
- keep only functions whose `__module__ == autosaxs.skill`

This mirrors `autosaxs/cli.py`’s approach and keeps GUI and CLI in sync.

### 3.2 Catalog UI (no categorization for now)

Do not categorize skills initially.

The left-side catalog is page-like (tabs), but tabs are not “categories”. Instead:
- one tab per skill (direct, one-click access), or
- a single “All skills” catalog tab showing skill pages/cards, with one-click selection

Rationale: avoids category drift and avoids dumping new skills into a catch-all bucket.

---

## 4. Run execution: isolated process runner

### 4.1 Runner strategy

Use `QProcess` as the canonical runner:
- async start/stop
- incremental stdout/stderr capture
- no UI freezing

Primary invocation form:

- `python -m autosaxs.cli <skill> ...`

This guarantees CLI parity and avoids importing heavy scientific dependencies into the GUI process.

### 4.2 Cancellation

Implement a two-phase cancel:
- `terminate()` then, after a timeout, `kill()`
- ensure process tree termination on Linux (process group/session if needed)

### 4.3 Output parsing

Parse CLI output lines:
- `key=value` into a dict
- use safe literal parsing (`ast.literal_eval`) when values look like lists/dicts
- persist `request.json`, `stdout.log`, `stderr.log`, `result.json`

---

## 5. Working directory conventions

Within the selected working directory (may be non-empty):

- `runs/latest/request.yml` (or `.json`)
- `runs/latest/stdout.log`
- `runs/latest/stderr.log`
- `runs/latest/result.yml` (or `.json`)
- `inputs/` (optional copies)
- outputs written either:
  - to `<workdir>/<skill_name>/...` (default), or
  - to `<workdir>/runs/latest/outputs/...` (alternate)

Default choice (implementation): **write outputs to `<workdir>/<skill_name>/...`**.

---

## 6. UI design choices (broad)

### 6.1 One window, three columns

Use a single `QSplitter` (left catalog tabs, middle runner, right artifacts/preview/workdir tree) as specified.

### 6.2 Middle column fixed ordering

The middle column is a vertical stack:
1) skill header (name + “?”)
2) input form
3) per-skill data panel
4) run control strip
5) live log

### 6.3 Rich path fields

Implement a reusable `PathField` widget:
- text entry (file/dir/glob)
- browse button (file or dir chooser depending on param)
- drag-and-drop target
- optional “preview matches” (executes *non-destructively*; displays matched files; does not change expansion semantics)

### 6.4 Artifact preview approach

Prefer previewing **skill-produced plots** (PNG) when available (fast, consistent, avoids duplicate science code).
For 1D `.dat` quick plots, allow reading using `autosaxs.utils.read_saxs` (preview-only, no processing).

---

## 7. Packaging boundaries (important)

- The GUI must never call processing routines directly (no `autosaxs.processor` usage).
- The GUI should treat skills as the only compute backend.
- Any “helper” behavior must be either:
  - input validation/presentation only, or
  - a composition of **skill runs** (future “recipes”), never in-process compute.

---

## 8. Input copying (design choice)

Input copying is controlled by a single checkbox in the UI: **“Copy inputs into working directory”**.

- If unchecked: skills run against the user-provided paths as-is.
- If checked: for each path-like input argument:
  - if the path is already under the working directory, **do not copy**
  - otherwise copy into `<workdir>/inputs/` (preserving basename with de-dup suffixing) and run the skill on the copied path

This keeps copying optional, avoids double-copying when inputs already live in the working directory, and improves portability of a session when desired.

