# Technical specification: Skills paradigm (autosaxs)

Specification of the **skills-based** processing model for the **autosaxs** package. This document describes how processing works, the architecture, inputs and outputs, and the role of UI/UX. It is the single source of truth for the new paradigm; the `.cache` file format is specified; other implementation details are kept minimal.

---

## 1. Purpose and scope

**Purpose:** Shift the central concept of autosaxs from a fixed **pipeline** to **skills**. A skill is a single processing routine that takes paths to data and optional arguments and returns paths to results; it operates on the file system. Pipelines are no longer hardcoded—they are composed per project (e.g. by AI or by the user) in small scripts that call skills in sequence. EventBus and interfaces (CLI, GUI) remain; skills may send progress messages through the EventBus so the UI can react. Optional content-hash caching (via a hidden **`.cache`** file and output-integrity check) replaces naive “fast-forward” logic; caching is **per sample**, and `.cache` uses a **list of records** (one record per sample; see §2.1) so when multiple samples write to the same directory, the cache file can store the necessary info for all of them individually. Common wrappers for hashing and batch application live in **`autosaxs/skill_wrap.py`**; skill entry points live in **`autosaxs/skill.py`**. The new **`autosaxs/skill.py`** (and other new scripts as needed) replaces the old **processor** module; all processing functionality migrates into `skill.py` or dedicated heavy modules.

**Scope:** Architecture, skill contract (inputs/outputs), UI/UX integration, the **manual command interface** (`autosaxs <command> ...`) for applying skills directly, main principles, and the list of skills with purpose/inputs/outputs. Not in scope: step-by-step migration plan. The `.cache` file format (YAML) is specified in §2 and §6.

---

## 2. How it works

- **Skills are pure functions**. Each skill has a single public entry point with an explicit signature suitable for both Python and CLI usage (§4.1.1). Internally, the skill MAY construct an input path dictionary and call a helper that follows the “path dict” convention (§4.1.2). In all cases, a skill reads only from the given input paths and writes only under the output directory; it returns a dictionary of output path roles to paths (strings or lists of paths).
- **Orchestration lives outside the package.** There is no built-in “pipeline” or “runner” type. A project script (or an AI-generated script) obtains paths (e.g. via EventBus-driven prompts to the user, or from a fixed layout), then calls skills in the desired order, passing the result paths of one skill as inputs to the next. Example: calibrate → integrate → subtract → fit_mixture; each step is one function call.
- **EventBus is the only side effect** allowed inside a skill. When the caller passes an EventBus, the skill may publish **MESSAGE** events (e.g. “Calibration: ring search…”, “Integration 3/10…”). Skills do not request files or choices; they do not subscribe to events. All interaction (directory choice, file upload, profile selection) is handled by the script and the interfaces that respond to EventBus requests.
- **Caching is optional, local to the skill output, and per sample.** There is no separate cache directory. When caching is enabled (default), a common wrapper in **`autosaxs/skill_wrap.py`** (used by skills in `skill.py`) uses a single hidden file **`.cache`** under the skill’s output directory, in **YAML format**. The structure is a **list of records** (see §2.1). Before running for a given sample, the skill (1) computes the current input hash, (2) looks up a record in `.cache` whose `hash` equals that value, and (3) if found, **verifies output integrity** using that record’s `output_paths` and `finish_date`: all listed paths must exist and their modification times must be **not later than** `finish_date`. If the hash matches and outputs are intact, the skill skips computation and returns the cached `output_paths` (with `from_cache: true`); otherwise it runs, writes outputs, and appends a new record (or replaces the invalid one and appends) in `.cache`.

### 2.1 Cache file format (`.cache`)

The file **`.cache`** is YAML and lives under the skill’s output directory. It has a single top-level key:

- **`records`** — A list of cache entries (one per sample when multiple samples share the same output directory, or a single entry for one sample). Order is append order; lookup is by **matching hash**, not by index.

Each element of `records` is an object with:

- **`hash`** — String. Digest of the inputs (selected path contents + config + relevant kwargs) used to key the cache. Same inputs produce the same hash.
- **`finish_date`** — String. ISO 8601 date/time (e.g. with `Z` or timezone) when the skill finished for this sample. Used for output-integrity checks: output files must have modification time not later than this date.
- **`output_paths`** — Object (dict). Same shape as the skill’s return value: keys are output role names (e.g. `subtracted_1d`, `diff_plot_path`), values are path strings or lists of path strings. Integrity is checked by collecting all path strings from this dict and verifying each file exists and has mtime ≤ `finish_date`.

**Lookup:** Find a record in `records` such that `record["hash"] == current_input_hash`. If none, run the skill and append a new record. If found, flatten `record["output_paths"]` to a list of paths and check that every path exists and has mtime not later than `record["finish_date"]`. If integrity fails, remove that record, run the skill, and append a new record. If integrity holds, return `record["output_paths"]` (and set `from_cache: true` in the returned dict).

---

## 3. Architecture

### 3.1 Layers and modules

- **Skills** — All skill **entry points** live in **`autosaxs/skill.py`**; common **wrappers** live in **`autosaxs/skill_wrap.py`** (and, if needed, other new scripts in `autosaxs/`). Small skills are implemented in `skill.py`; “heavy” skills (e.g. fit_mixture, guinier_analysis) are thin wrappers in `skill.py` that delegate to dedicated modules (e.g. `autosaxs/mixture.py`, `autosaxs/guinier`). No skill logic in the controller. **`autosaxs/skill.py`** and **`autosaxs/skill_wrap.py`** (and related modules) **replace the old `processor` module** for new flows; processing functionality migrates into skills or dedicated heavy modules.
- **Wrappers in `skill_wrap.py`** — (1) **Hashing and cache logic**: computing input hash, reading/writing the **`.cache`** file (YAML with a **`records`** list; each record has `hash`, `finish_date`, `output_paths`—see §2.1). Lookup by matching hash; output integrity per record by checking that all paths in `output_paths` exist and have mtime not later than `finish_date`. (2) **Batch application** (`apply_batch`): applying a skill over many inputs (e.g. many images to integrate, many profiles for fit_mixture) with consistent options and optional caching; **caching is per sample**. Parameters **`single_output_dir`** (when True, all samples write to the same output directory; when False, one subdirectory per sample) and **`stem_from_keys`** (which input key(s) to use to derive the per-sample subdir name) control batch behaviour. When multiple samples write to the same output directory, `.cache` holds many records (one per sample) so each sample can be validated or skipped independently by hash match and integrity check. Scripts and skills in `skill.py` use these wrappers to avoid duplication.
- **EventBus** — Unchanged. Single channel for request–response (directory, files, choices, pipeline/profile selection) and for one-way **MESSAGE** (progress, status). Skills only publish **MESSAGE** when given an EventBus; they do not participate in request–response.
- **Interfaces** — **CLI** and **GUI** (and script-based API) remain. They subscribe to EventBus request events and publish responses. They also subscribe to **MESSAGE** to display progress. The script that composes skills is responsible for issuing requests (e.g. “ask for directory”) and passing the resolved paths into skills; the script may use the same EventBus so that the chosen interface prompts the user and then the script calls skills with the returned paths.
- **Utils, context** — `utils` and `context` remain for I/O, config, and path conventions. The old **processor** module is superseded by `skill.py` and dedicated modules as above.

### 3.2 Data flow

1. **Script** creates EventBus, optionally connects CLI or GUI (so the user can be prompted).
2. **Script** obtains working directory, config path, and initial file paths (by publishing **DIRECTORY_REQUESTED** / **FILE_REQUESTED** etc. and consuming responses, or by reading a fixed layout).
3. **Script** calls skill functions in sequence, e.g.  
   `out_cal = calibrate(calib_image, config_path, output_dir, mask=..., use_cache=True)`  
   `out_int = integrate(images, out_cal["integrator_dir"], output_dir, npt=1000, use_cache=True)`  
   (Caching is on by default; pass `use_cache=False` or set `--no-cache` in CLI.)  
   Each skill returns a dict of output path roles (e.g. `integrator_dir`, `integrated_1d`).
4. **Skills** read from `input_paths` and optional `config`, write under `output_dir`, optionally publish **MESSAGE** on `event_bus`, and by default use the `.cache` file + output-integrity check (`use_cache=True`).
5. **Interfaces** display messages and respond to requests; they do not call skills. Control flow is: script ↔ EventBus ↔ interface; script → skills.

### 3.3 Manual commands for skills (`autosaxs <command> ...`)

The package MUST provide a command-line interface so that users can apply skills manually, without writing a script:

```bash
autosaxs <command> <args> <keys=kwargs>
```

This command runner is **not** a “pipeline runner”: it is a thin dispatcher that maps each `<command>` to exactly one skill entry point and returns/writes the same outputs as calling the skill from Python.

#### 3.3.1 Command ↔ skill mapping rule (hard requirement)

- **Every skill MUST have a corresponding command** with the same name (or a stable, explicitly documented alias).
- **Command arguments MUST coincide with the skill entry point arguments.**
  - **Positional arguments**: the CLI positional arguments MUST be in the **same order** as the skill function’s positional parameters.
  - **Keyword options**: CLI flags/keys MUST map 1:1 to the skill function’s keyword parameters (including names and default values, modulo CLI naming conventions like `--q-min` → `q_min`).
  - No “extra” CLI-only parameters are allowed (except standard global flags like `--help` and `--version`), because they would break the guarantee that “CLI mirrors the skill”.

This rule exists so the CLI can be treated as “manual skill invocation”, and so documentation/tests can validate parity between Python and CLI.

#### 3.3.2 Standard CLI conventions

- **Output directory**: every command MUST support `--output-dir <path>` (default: current working directory). This maps to the skill argument `output_dir`.
- **Caching**: every command MUST support `--no-cache` (default caching on). This maps to the skill argument `use_cache` (CLI uses `--no-cache` to set `use_cache=False`).
- **EventBus**: CLI commands do not accept `event_bus`; they run without a bus by default. (Progress may be printed to stderr; this is an implementation detail.)

#### 3.3.3 Examples (required to work as shown)

```bash
# Detector calibration from a TIFF calibrant image and config
autosaxs calibrate AgBh.tif config.conf

# Buffer subtraction from 1D curves with method and q-window kwargs
autosaxs subtract sample.dat buffer.dat --method match_tail --q-min 4.0 --q-max 6.0
```

---

## 4. Inputs and outputs

### 4.1 Skill function signature (convention)

To satisfy the **command ↔ skill mapping rule** (§3.3.1), every skill MUST expose a **CLI-compatible public entry point** with an explicit, stable Python signature. The entry point MAY internally construct an `input_paths` dictionary and delegate to a private implementation, but the public signature is normative.

#### 4.1.1 Public skill entry point (CLI-compatible)

Every skill entry point MUST:

- Accept its **primary path inputs as positional arguments** (e.g. `calib_image`, `config_path`, `sample_1d`, `buffer_1d`).
- Accept `output_dir` as a parameter (exposed in CLI as `--output-dir`).
- Accept `use_cache: bool = True` as a parameter (exposed in CLI as `--no-cache`).
- Expose skill-specific options as explicit keyword parameters (so CLI flags map 1:1).
- Return only paths: `dict[str, str | list[str]]`.

Conventions to ensure CLI parity:

- **No aliasing of option values**: when a skill parameter is an enum-like string (e.g. `method`), the CLI MUST accept the exact same string values as the skill (project convention), without introducing hyphenated aliases like `tail-match` unless the skill itself uses that exact value.
- **Required positional inputs stay required**: if a skill requires a path input (e.g. `config_path` for calibration), the corresponding CLI command MUST require it as a positional argument as well.

Example shape (illustrative):

```python
def subtract(
    sample_1d: str,
    buffer_1d: str,
    output_dir: str = ".",
    *,
    method: str = "match_tail",
    q_min: float | None = None,
    q_max: float | None = None,
    use_cache: bool = True,
) -> dict[str, str]:
    ...
```

#### 4.1.2 Internal implementation helper (optional)

Skills MAY additionally use an internal helper that follows the “path dict” convention for composability in scripts:

- **`input_paths`** — `dict[str, str | list[str]]`. Keys are semantic roles (e.g. `calib_image`, `config`, `integrator_dir`, `images`, `profile`, `buffer_1d`, `sample_1d`). Values are a single path or a list of paths. The skill reads only from these paths (and from options below); it does not read from global state.
- **`output_dir`** — `str`. Directory under which the skill writes all outputs (possibly in subdirectories). The skill does not write outside this tree.
- **`config`** — `dict | None`. Optional in-memory config (e.g. loaded by the script from `input_paths["config"]`).
- **`event_bus`** — `EventBus | None`. If provided, the skill may call `event_bus.publish(EventType.MESSAGE, {"text": "..."})` for progress. No other events.
- **`use_cache`** — `bool`, default **True**. If True, the skill (or a wrapper in `skill_wrap.py`) uses the `.cache` file (YAML: list of records with `hash`, `finish_date`, `output_paths`—see §2.1) and per-record output-integrity check; skip computation when a matching record exists and its output paths are intact.
- **`**kwargs`** — Skill-specific options (e.g. `q_range_nm`, `npt`, `mask_mode`). Documented per skill.

**Return:** `dict[str, str | list[str]]` — output path roles to paths. Only paths; no in-memory objects. Example keys: `integrator_dir`, `refined_path`, `integrated_1d`, `subtracted_1d`, `output_subdir`, `comparison_path`, `results_csv_path`.

**Documentation:** Every skill function MUST have a **standard triple-quoted docstring** (Python convention). The docstring must be short and describe: (1) **main purpose** of the skill, (2) **inputs** (relevant `input_paths` roles and notable `**kwargs`), (3) **outputs** (returned path roles). This is the single place for the skill’s contract in code.

**Tests:** Each skill MUST have **tests** (e.g. in the package test suite) that cover the skill’s contract and main behaviour. These tests MUST be **executed by the coding agent every time that skill is modified** (as part of the change workflow), to guard against regressions.

### 4.2 Input/output roles (semantic names)

Roles are stable and documented so that scripts (and AI) can wire skills. Examples:

- **calibrate:** `calib_image`, `config`, optional `mask` → `integrator_dir`, `refined_path`.
- **integrate:** `images` (2D), `integrator_dir` → `integrated_1d` (list).
- **integrate_proxy:** `image` (single `.tif` path or directory of `.tif`), `config`, optional center args `cy`, `cx` → `integrated_1d` (same output contract as integrate, x-axis in pixels). If `cy` and `cx` are both `None`, center is estimated via `ring_analysis` and its debug plots are written to `output_dir`; if center estimation fails, skill prints a warning, writes no `.dat`, and returns empty output.
- **subtract:** `sample_1d`, `buffer_1d` (paired or paired by convention) → `subtracted_1d`.
- **plot_2d:** `image` (2D) → `plot_2d`.
- **fit_mixture:** `profile` (one 1D curve) → `output_subdir`, `comparison_path`, `distributions_path`, `results_csv_path`.

Exact keys and multiplicity (single path vs list) are defined per skill in code and docstrings; this spec only establishes that inputs and outputs are path dictionaries with semantic role names.

---

## 5. UI and UX

### 5.1 What is UI

- **UI** is the same as today: **CLI** (stdin/print, file monitoring) and **GUI** (e.g. CustomTkinter dialogs). Both are **interfaces** that subscribe to EventBus. They handle:
  - **DIRECTORY_REQUESTED** → prompt user → **DIRECTORY_SPECIFIED**
  - **FILE_REQUESTED** → prompt or monitor → **FILE_UPLOADED** / **FILE_UPLOAD_CANCELED**
  - **CHOICE_REQUESTED** → prompt → **OPTION_CHOSEN** / **OPTION_CHOICE_CANCELED**
  - **PIPELINE_STEPS_REQUESTED** → step selection → **PIPELINE_STEPS_SPECIFIED**
  - **PROFILE_SELECTION_REQUESTED** → profile selection → **PROFILE_SELECTION_SPECIFIED**
  - **MESSAGE** → display text (progress, errors, status)

No UI component calls a skill. The **script** (or a thin orchestrator in the script) publishes requests and subscribes to responses; once it has paths, it calls skills. So the same UI can serve both the legacy pipeline (controller publishing requests) and the new paradigm (script publishing requests).

### 5.2 What is UX

- **User flow:** User runs a script (or an entry point that uses the script pattern). The script may first ask for directory and files via EventBus; the chosen interface (CLI or GUI) shows prompts. After paths are resolved, the script runs skills one by one. During long-running skills, the user sees progress messages (e.g. “Integration 3/10…”) because the skill publishes **MESSAGE** when `event_bus` is passed. Errors can be reported the same way or by the script after a skill raises.
- **Determinism and caching:** Same inputs (data + options) produce the same outputs. With `use_cache=True` (default), re-runs with unchanged data skip work (a record in `.cache` has matching hash and intact `output_paths`) and return the cached paths, improving UX for repeated or resumed runs.
- **Composability:** The user (or AI) composes a sequence of skills in a small script. There is no single “pipeline app” with a fixed menu of steps; the script defines the flow. This allows per-project workflows without changing the package.

---

## 6. Main principles and features

- **Functional style:** Skills are functions, not classes. No `Skill` base class or OOP hierarchy. **Skill names are verbs** (e.g. calibrate, integrate, subtract, plot, plot_2d, fit_mixtur); the entry points are `calibrate`, `integrate`, etc.
- **File-system contract:** Inputs and outputs are paths. Internal use of in-memory objects (e.g. integrator) is an implementation detail; the contract is path-in, path-out.
- **Single side effect:** The only impurity is optional EventBus messaging. Skills do not request files or choices; they only publish **MESSAGE** when given an EventBus.
- **No built-in pipeline or runner:** Pipelines are not a library abstraction. They are sequences of skill calls in project scripts.
- **Optional caching via `.cache` and output integrity; per-sample in batch:** A single hidden file **`.cache`** (YAML) under the skill’s output directory stores a **`records`** list (§2.1). Each record has **`hash`** (input digest), **`finish_date`** (ISO date when the skill finished), and **`output_paths`** (dict of output role → path(s), same shape as the skill return value). There is **one** record when a single sample uses the directory; **many** records when multiple samples write to the same batch directory, so caching remains **per sample**. No separate cache directory. Lookup is by **matching hash** (not by index). For the matching record, integrity is verified: all paths in `output_paths` must exist and have modification time not later than `finish_date`. Integrity uses paths and mtime only, not content hash. Enables correct reuse when inputs are unchanged; replaces naive “if file exists, skip.” Caching is **on by default** (`use_cache=True`).
- **Wrappers in `skill_wrap.py`:** Wrappers for hashing/cache logic (`.cache` read/write, integrity check) and for batch skill application (`apply_batch`, with `single_output_dir` and `stem_from_keys`) live in `autosaxs/skill_wrap.py` and are used by skills in `skill.py` and by scripts.
- **Skill scripts replace processor:** **`autosaxs/skill.py`** (entry points) and **`autosaxs/skill_wrap.py`** (wrappers) hold the skill layer; heavy logic stays in dedicated modules (e.g. `mixture.py`, `guinier`). The old **processor** module is superseded for new flows.
- **One place for entry points:** All skill entry points live in `autosaxs/skill.py`; wrappers live in `autosaxs/skill_wrap.py`. Heavy logic stays in dedicated modules (e.g. `mixture.py`, `processor`); `skill.py` exposes thin wrappers with the standard signature.
- **Skill documentation:** Each skill has a **standard triple-quoted docstring** with: main purpose, inputs (path roles and notable kwargs), outputs (returned path roles). Short and consistent so scripts and AI can rely on it.
- **Skill tests:** Each skill has **tests** that MUST be **run by the coding agent every time that skill is modified**. Tests cover the skill’s contract and main behaviour; running them on change is required to avoid regressions.
- **Backward compatibility during transition:** The new paradigm is implemented in `skill.py` and related modules. Existing controller and pipeline code remain until the new approach is validated; then obsolete code (including `processor`) can be removed. EventBus and interfaces persist unchanged.

---

## 7. Relation to existing pipeline spec

The document **`pipeline_interactive_spec.md`** describes the current **controller-based** pipeline: fixed steps, Controller publishing requests, interfaces responding, and a single `pipeline_interactive()` flow. The **skills paradigm** does not replace that spec until the legacy pipeline is retired. Until then:

- **Skills paradigm** (this document): skills as functions, no runner, composition in scripts, EventBus for messages only from skills.
- **Pipeline interactive** (existing spec): Controller-driven, step selection, file/choice requests, full request–response EventBus usage.

Both can coexist: the same EventBus and interfaces serve either the Controller (legacy) or a script that calls skills (new). When the legacy pipeline is removed, this document becomes the primary architectural spec for autosaxs processing.

---

## 8. List of skills

Each skill is a single processing routine with the standard signature (§4.1), a **triple-quoted docstring** (purpose, inputs, outputs), and **tests** run by the coding agent on every change to that skill. Below: purpose, main inputs, main outputs. Exact role names and options are defined in code and docstrings; the docstring must reflect this contract.

| Skill | Purpose | Main inputs | Main outputs |
|-------|---------|-------------|--------------|
| **calibrate** | Calibrate detector geometry via ring analysis (Laplacian/GMM, DBSCAN, ``refine``). All calibration plots (ring pipeline, q/I curve, mask) under ``calibration_plots_dir``. | `calib_image`, `config` (with ``ring_analysis`` + ``detector_geometry``), optional `mask` | `integrator_dir`, `refined_path`, `calibration_plots_dir`, `calibration_curve_plot_path`, `calibration_mask_path` |
| **integrate** | Integrate 2D SAXS images to 1D curves (q, I, σ) using a calibrated integrator. | `images` (2D), `integrator_dir` | `integrated_1d` (list of paths) |
| **integrate_proxy** | Integrate 2D `.tif` image input(s) to 1D curves without detector calibration. Public entry point is `integrate_proxy(image, output_dir=".", *, cy=..., cx=..., config=..., npt=..., use_cache=True)`. `image` accepts either a single `.tif` path or a directory of `.tif` files. `cy` and `cx` must be both `None` or both floats. If both are `None`, center is estimated with `ring_analysis` using `config`, and all ring-analysis debug plots are written to `output_dir`. If center estimation fails, the skill prints a warning, writes no `.dat`, and returns empty output. The resulting `.dat` keeps the standard format but uses pixel radius (`r_px`) as x-axis and records this in meta. | `image` (single `.tif` file path or directory of `.tif`), `config`, optional `cy`, `cx` | `integrated_1d` (single path or list; empty when center estimation fails) |
| **subtract** | Subtract buffer from sample 1D profile (e.g. match-tail scaling), write subtracted curve. | `sample_1d`, `buffer_1d` (paired or by convention) | `subtracted_1d`, `diff_plot_path`, `sub_plot_path` |
| **plot** | Generate standard plots for a 1D profile: Guinier, Kratky, log–log; optionally write a Guinier-range .dat. | `profile` (1D), optional guinier region | `guinier_plot_path`, `kratky_plot_path`, `loglog_plot_path`, optional `guinier_dat_path` |
| **plot_2d** | Render 2D SAXS TIFF input(s) to PNG using logarithmic intensity and viewer-consistent defaults. Public entry point is `plot_2d(image: str, output_dir=".", *, use_cache=True, ...)`, where `image` accepts either a single `.tif` file path or a directory containing `.tif` files. The transform is `log1p(I)` (`ln(1+I)`). | `image` (single `.tif` file path or directory of `.tif`) | `plot_2d_png` (single path for single-file input; list for directory input) |
| **guinier_analysis** | Run Guinier analysis on a 1D profile (first5, first10, autorg, adaptive; chosen = adaptive). Writes results file and ATSAS-format .dat for downstream (e.g. DATGNOM). Uses `autosaxs.guinier`. | `profile` (1D) | `results_path`, `atsas_dat_path`, `guinier_region_path` (yml) |
| **fit_mixture** | Run MIXTURE fits (1-/2-/3-phase × Gaussian/Schultz–Zimm, sphere-only), select best by BIC, write comparison plot, distribution plot, results CSV. | `profile` (1D subtracted) | `output_subdir`, `comparison_path`, `distributions_path`, `results_csv_path` |
| **fit_bodies** | Run ATSAS **bodies** on a 1D profile for multiple shapes; export fits (fir, PNG, yml, csv). | `profile` (1D) | `output_subdir`, bodies fit files (fir, png, yml, csv) |
| **fit_dammif** | Run ATSAS **dammif** (ab initio shape reconstruction) on a 1D profile; produce shape models and descriptors. | `profile` (1D), optional `gnom_path` | `output_subdir`, dammif output files |
| **report_individual** | Build individual PDF report for one sample from an existing pipeline directory. Scans directory for paths matching basename, assembles report data, writes PDF. Main logic in `report.py`. | `directory`, `basename` (convention: not in `input_paths`; passed as arguments) | `report_pdf_path` |
| **report_summary** | Build summary PDF report from an existing pipeline directory. Discovers samples from subtracted/ and related dirs, writes summary PDF. Main logic in `report.py`. | `directory` (convention: passed as argument) | `report_pdf_path` |

**Wrapper features (`skill_wrap.apply_batch`):** Batch application supports **`single_output_dir`** (default False): when True, all samples in a batch write to the same output directory; when False, one subdirectory per sample (stem from **`stem_from_keys`**). **`stem_from_keys`** specifies which input key(s) to use to derive the per-sample subdir name (e.g. `"profile"`, `"images"`, `"image"`). Output file stems are normalized: if an input stem starts with `sub_` or `int_`, that prefix is stripped before saving new data (see `_strip_sub_int_prefix` in `skill_wrap.py`).

