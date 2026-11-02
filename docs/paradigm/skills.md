# Skills paradigm (autosaxs)

Package processing paradigm: skills as pure path-in / path-out functions, CLI↔skill parity, and the **`.cache`** YAML contract. Module inventory lives in [`AGENTS.md`](../../AGENTS.md). Per-skill I/O catalog: generated `autosaxs-docs/skills_reference.md` and skill docstrings (`autosaxs get-docs` / `get-skills`).

---

## 1. Purpose and scope

**Purpose:** Shift the central concept of autosaxs from a fixed **pipeline** to **skills**. A skill is a single processing routine that takes paths to data and optional arguments and returns paths to results; it operates on the file system. Pipelines are no longer hardcoded—they are composed per project (e.g. by AI or by the user) in small scripts that call skills in sequence. EventBus and interfaces (CLI, GUI) remain; skills may send progress messages through the EventBus so the UI can react. Optional content-hash caching (via a hidden **`.cache`** file and output-integrity check) replaces naive “fast-forward” logic; caching is **per sample**, and `.cache` uses a **list of records** (one record per sample; see §2.1) so when multiple samples write to the same directory, the cache file can store the necessary info for all of them individually. Common wrappers for hashing and batch application live in **`autosaxs/skill/skill_wrap.py`**; skill **entry points** live in the **`autosaxs/skill/`** package (one module or subpackage per skill, discovered via `autosaxs.skill.list_skills()`). That skill layer (and dedicated heavy modules such as `mixture`, `guinier`, `processor`) replaces the old monolithic **processor** usage for new flows.

**Scope:** Architecture, skill contract (inputs/outputs), UI/UX integration, the **manual command interface** (`autosaxs <command> ...`) for applying skills directly, and main principles. Not in scope: step-by-step migration plan or per-skill I/O catalog (see generated docs). The `.cache` file format (YAML) is specified in §2 and §6.

---

## 2. How it works

- **Skills are pure functions**. Each skill has a single public entry point with an explicit signature suitable for both Python and CLI usage (§4.1.1). Internally, the skill MAY construct an input path dictionary and call a helper that follows the “path dict” convention (§4.1.2). In all cases, a skill reads only from the given input paths and writes only under the output directory; it returns a dictionary of output path roles to paths (strings or lists of paths).
- **Orchestration lives outside the package.** There is no built-in “pipeline” or “runner” type. A project script, liveview `plan_for`, or CLI one-shot obtains paths (fixed layout or user input), then calls skills in the desired order, passing the result paths of one skill as inputs to the next. Example: calibrate → integrate → subtract → model_mixture; each step is one function call.
- **EventBus is the only side effect** allowed inside a skill. When the caller passes an EventBus, the skill may publish **MESSAGE** events (e.g. “Calibration: ring search…”, “Integration 3/10…”). Skills do not request files or choices; they do not subscribe to events.
- **Caching is optional, local to the skill output, and per sample.** There is no separate cache directory. Caching is **off by default** (`use_cache=False`; see §4.1.1). When the caller passes `use_cache=True` (CLI: `--cache`), a common wrapper in **`autosaxs/skill/skill_wrap.py`** uses a single hidden file **`.cache`** under the skill’s output directory, in **YAML format**. The structure is a **list of records** (see §2.1). Before running for a given sample, the skill (1) computes the current input hash, (2) looks up a record in `.cache` whose `hash` equals that value, and (3) if found, **verifies output integrity** using that record’s `output_paths` and `finish_date`: all listed paths must exist and their modification times must be **not later than** `finish_date`. If the hash matches and outputs are intact, the skill skips computation and returns the cached `output_paths` (and may set `from_cache: true`); otherwise it runs, writes outputs, and appends a new record (or replaces the invalid one and appends) in `.cache`. **Heavy skills** (e.g. calibrate, integrate, subtract, `fit_*`) SHOULD emit a one-line hint when caching is off that re-running with `use_cache=True` / `--cache` can save time when inputs are unchanged.

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

- **Skills** — Public **entry points** are functions named after the skill (`subtract`, `calibrate`, …), implemented under **`autosaxs/skill/`** (e.g. `autosaxs/skill/subtract.py`, `autosaxs/skill/calibrate/`, `autosaxs/skill/model_mixture/`). The package **`autosaxs.skill`** re-exports them and exposes **`list_skills()`** for CLI/GUI discovery. Small skills are implemented in their module; heavy skills are thin entry points that delegate to dedicated code (e.g. `model_mixture/mixture.py`, `fit_guinier/guinier.py`, `core/processor.py`). No skill logic in the controller.
- **Wrappers in `autosaxs/skill/skill_wrap.py`** — (1) **Hashing and cache logic**: computing input hash, reading/writing the **`.cache`** file (YAML with a **`records`** list; each record has `hash`, `finish_date`, `output_paths`—see §2.1). Lookup by matching hash; output integrity per record by checking that all paths in `output_paths` exist and have mtime not later than `finish_date`. (2) **Batch application** (`apply_batch`): applying a skill over many inputs (e.g. many images to integrate, many profiles for model_mixture) with consistent options and optional caching; **caching is per sample**. Parameters **`single_output_dir`** (when True, all samples write to the same output directory; when False, one subdirectory per sample) and **`stem_from_keys`** (which input key(s) to use to derive the per-sample subdir name) control batch behaviour. When multiple samples write to the same output directory, `.cache` holds many records (one per sample) so each sample can be validated or skipped independently by hash match and integrity check. Skill modules use these wrappers to avoid duplication.
- **EventBus** — Optional progress channel. Skills may publish **MESSAGE** when given an EventBus; they do not request files or choices.
- **Interfaces** — Package CLI (`autosaxs <skill> …`), skills console, and liveview compose skills outside the skill functions themselves. See [`AGENTS.md`](../../AGENTS.md).
- **Utils, context** — `utils` and `context` remain for I/O, config, and path conventions. Low-level routines may live in `core/`; new flows call skills in `autosaxs/skill/`.

### 3.2 Data flow

1. **Orchestrator** (project script, liveview `plan_for` + executor, or CLI one-shot) obtains paths from a fixed layout or user input.
2. **Orchestrator** calls skill functions in sequence, e.g.  
   `out_cal = calibrate(calib_image, config_path, output_dir, mask=...)`  
   `out_int = integrate(images, out_cal["integrator_dir"], output_dir, npt=1000, use_cache=True)`  
   (Caching is **off** by default; pass `use_cache=True` or CLI `--cache` when re-running unchanged inputs.)  
   Each skill returns a dict of output path roles (e.g. `integrator_dir`, `integrated_1d`).
3. **Skills** read from given input paths and optional `config`, write under `output_dir`, optionally publish **MESSAGE** on an `event_bus`, and use the `.cache` file + output-integrity check only when `use_cache=True`.
4. Product GUIs do not call skills in-process for execution; they launch the CLI (`SkillRunner`). Historical EventBus request/response orchestration belonged to the removed interactive Controller — see [`../archive/legacy-interactive-pipeline.md`](../archive/legacy-interactive-pipeline.md).

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
- **Caching**: every command MUST support enabling cache explicitly. The skill argument defaults to `use_cache=False`. The CLI exposes **`--cache`** (set `use_cache=True`) and **`--no-cache`** (set `use_cache=False`); with no flag, the skill default applies (caching off).
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
- Accept `use_cache: bool = False` as a parameter (CLI: `--cache` to enable, `--no-cache` to force off).
- Expose skill-specific options as explicit keyword parameters (so CLI flags map 1:1).
- Return primarily paths: `dict[str, str | list[str]]` (see **Return value nuances** below).

**Main input path rule (a requirement for skills under `autosaxs/skill/`):**

- For most of skills whose primary input is a dataset path (2D image(s) or 1D profile(s)), the **main positional path argument** SHOULD accept a **single string** that can be:
  - a **single file path**
  - a **directory path**
  - an **arbitrary glob expression**
- The function name and parameter names are part of the public API.
This rule is applied if appropriate (for instance, it makes sense to integrate many images at once, but it does make sense to calibrate on many images at once).

**Expansion semantics (when the main positional arg is a directory or glob):**

- **Sorting:** expanded file lists MUST be sorted lexicographically for determinism.
- **Empty match:** if a glob or directory expansion yields **zero files**, the skill MUST raise `FileNotFoundError` (not return an empty result).
- **Directory expansion is non-recursive:**
  - For 2D TIFF skills, a directory expands to `*.tif` in that directory (non-recursive).
  - For 1D skills, a directory expands to `*.dat` in that directory (non-recursive).
- **Batch execution and return shape:** for skills that can run per-file (`plot`, `plot_2d`, `fit_guinier`, `fit_*`, etc.), directory/glob inputs MUST execute **one-per-file** and return lists of outputs (consistent with the internal `apply_batch` convention).

**Return value nuances (minor, accepted in implementation):**

- The normative contract is **path roles → file paths** (or lists of paths for batch). A few extra return keys are tolerated and need not be “fixed” for parity:
  - **`from_cache: true`** on cache hits (boolean flag, not a path).
  - **Diagnostic strings** alongside optional plot paths (e.g. `fit_vs_exp_png_error` when a PNG could not be written).
  - **Small non-path metadata** in report skills (e.g. `fragments_found` count).
- **Semantic results** that belong in reports or `.dat` metadata (e.g. subtraction `correctness`, fit metrics) MUST be written to **on-disk artifacts** (report fragments, YAML header metadata, CSV/YAML under `output_subdir`), not returned as the primary contract. Callers discover them by reading those files.

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
    method: str = "minimal_ratio",
    q_min: float | None = None,
    q_max: float | None = None,
    use_cache: bool = False,
) -> dict[str, str]:
    ...
```

#### 4.1.2 Internal implementation helper (optional)

Skills MAY additionally use an internal helper that follows the “path dict” convention for composability in scripts:

- **`input_paths`** — `dict[str, str | list[str]]`. Keys are semantic roles (e.g. `calib_image`, `config`, `integrator_dir`, `images`, `profile`, `buffer_1d`, `sample_1d`). Values are a single path or a list of paths. The skill reads only from these paths (and from options below); it does not read from global state.
- **`output_dir`** — `str`. Directory under which the skill writes all outputs (possibly in subdirectories). The skill does not write outside this tree.
- **`config`** — `dict | None`. Optional in-memory config (e.g. loaded by the script from `input_paths["config"]`).
- **`event_bus`** — `EventBus | None`. If provided, the skill may call `event_bus.publish(EventType.MESSAGE, {"text": "..."})` for progress. No other events.
- **`use_cache`** — `bool`, default **False**. If True, the skill (or a wrapper in `skill_wrap.py`) uses the `.cache` file (YAML: list of records with `hash`, `finish_date`, `output_paths`—see §2.1) and per-record output-integrity check; skip computation when a matching record exists and its output paths are intact.
- **`**kwargs`** — Skill-specific options (e.g. `q_range_nm`, `npt`, `mask_mode`). Documented per skill.

**Return:** `dict[str, str | list[str]]` — output path roles to paths (see §4.1.1 **Return value nuances** for minor extra keys). Example keys: `integrator_dir`, `refined_path`, `integrated_1d`, `subtracted_1d`, `output_subdir`, `comparison_path`, `results_csv_path`.

**Documentation:** Every skill function MUST have a **standard triple-quoted docstring** (Python convention). The docstring must be short and describe: (1) **main purpose** of the skill, (2) **inputs** (relevant `input_paths` roles and notable `**kwargs`), (3) **outputs** (returned path roles). This is the single place for the skill’s contract in code.

**Tests:** Each skill MUST have **tests** (e.g. in the package test suite) that cover the skill’s contract and main behaviour. These tests MUST be **executed by the coding agent every time that skill is modified** (as part of the change workflow), to guard against regressions.

### 4.2 Input/output roles (semantic names)

Roles are stable and documented so that scripts (and AI) can wire skills. Examples:

- **calibrate:** `calib_image`, `config`, optional `mask` → `integrator_dir`, `refined_path`.
- **integrate:** `images` (2D), `integrator_dir`, optional `mask` override → `integrated_1d` (list).
- **integrate_proxy:** `image` (file/dir/glob of `.tif`), `config`, optional center args `cy`, `cx` → `integrated_1d` (list for multi-input). If `cy` and `cx` are both `None`, center is estimated via `ring_analysis` and its debug plots are written to `output_dir`; if center estimation fails for a given image, that item yields no `.dat` for that image, but empty **input** expansions are still an error (see §4.1.1).
- **subtract:** `sample_1d`, `buffer_1d` (paired or paired by convention) → `subtracted_1d`.
- **plot_2d:** `image` (2D) → `plot_2d`.
- **model_mixture:** `profile` (one 1D curve) → `output_subdir`, `comparison_path`, `distributions_path`, `results_csv_path`.

Exact keys and multiplicity (single path vs list) are defined per skill in code and docstrings; this spec only establishes that inputs and outputs are path dictionaries with semantic role names.

---

## 5. UI and UX

### 5.1 What is UI

Product GUIs (skills console, liveview) introspect skills for metadata and run them via CLI subprocess (`SkillRunner`). They do not call skill functions in-process for execution (GNOM Adjust preview in liveview is the documented exception). Skills may publish **MESSAGE** on an EventBus for progress when a bus is passed; they do not drive prompts.

### 5.2 What is UX

- **User flow:** User runs a script (or an entry point that uses the script pattern). The script may first ask for directory and files via EventBus; the chosen interface (CLI or GUI) shows prompts. After paths are resolved, the script runs skills one by one. During long-running skills, the user sees progress messages (e.g. “Integration 3/10…”) because the skill publishes **MESSAGE** when `event_bus` is passed. Errors can be reported the same way or by the script after a skill raises.
- **Determinism and caching:** Same inputs (data + options) produce the same outputs. With `use_cache=True`, re-runs with unchanged data skip work (a record in `.cache` has matching hash and intact `output_paths`) and return the cached paths, improving UX for repeated or resumed runs. Caching is opt-in (default off); heavy skills may remind the user that `--cache` is available.
- **Composability:** The user (or AI) composes a sequence of skills in a small script. There is no single “pipeline app” with a fixed menu of steps; the script defines the flow. This allows per-project workflows without changing the package.

---

## 6. Main principles and features

- **Functional style:** Skills are functions, not classes. No `Skill` base class or OOP hierarchy. **Skill names are verbs** (e.g. calibrate, integrate, subtract, plot, plot_2d, fit_mixtur); the entry points are `calibrate`, `integrate`, etc.
- **File-system contract:** Inputs and outputs are paths. Internal use of in-memory objects (e.g. integrator) is an implementation detail; the contract is path-in, path-out.
- **Single side effect:** The only impurity is optional EventBus messaging. Skills do not request files or choices; they only publish **MESSAGE** when given an EventBus.
- **No built-in pipeline or runner:** Pipelines are not a library abstraction. They are sequences of skill calls in project scripts.
- **External tool invocation standard (hard requirement):** Skills that invoke external command-line tools (e.g. ATSAS programs such as `gnom`, `dammif`, `bodies`, etc.) MUST invoke them **by executable name from `PATH`** (e.g. `gnom ...`). This is required for portability across environments where ATSAS is installed and exposed via `PATH`.
- **Optional caching via `.cache` and output integrity; per-sample in batch:** A single hidden file **`.cache`** (YAML) under the skill’s output directory stores a **`records`** list (§2.1). Each record has **`hash`** (input digest), **`finish_date`** (ISO date when the skill finished), and **`output_paths`** (dict of output role → path(s), same shape as the skill return value). There is **one** record when a single sample uses the directory; **many** records when multiple samples write to the same batch directory, so caching remains **per sample**. No separate cache directory. Lookup is by **matching hash** (not by index). For the matching record, integrity is verified: all paths in `output_paths` must exist and have modification time not later than `finish_date`. Integrity uses paths and mtime only, not content hash. Enables correct reuse when inputs are unchanged; replaces naive “if file exists, skip.” Caching is **off by default** (`use_cache=False`); enable with `use_cache=True` or CLI `--cache`.
- **Wrappers in `skill_wrap.py`:** Wrappers for hashing/cache logic (`.cache` read/write, integrity check) and for batch skill application (`apply_batch`, with `single_output_dir` and `stem_from_keys`) live in `autosaxs/skill/skill_wrap.py` and are used by skill modules and scripts.
- **Skill package is the primary API:** **`autosaxs/skill/`** holds entry points; heavy logic stays in submodules and `core/`. Product orchestration uses skills (and liveview `plan_for`). The former interactive Controller package is gone — see `../archive/legacy-interactive-pipeline.md`.
- **One place for discovery:** **`autosaxs.skill.list_skills()`** is the registry for CLI/GUI; each skill’s public function lives in its module under `autosaxs/skill/`.
- **Skill documentation:** Each skill has a **standard triple-quoted docstring** with: main purpose, inputs (path roles and notable kwargs), outputs (returned path roles). Short and consistent so scripts and AI can rely on it.
- **Skill tests:** Each skill has **tests** that MUST be **run by the coding agent every time that skill is modified**. Tests cover the skill’s contract and main behaviour; running them on change is required to avoid regressions.

---

## 7. Relation to GUIs

- **Skills paradigm** (this document): skills as functions, composition in scripts / liveview planner, EventBus only for optional progress from skills.
- **Liveview:** Session + SampleStore + `plan_for` — [`../architecture/liveview/overview.md`](../architecture/liveview/overview.md), [`../architecture/liveview/behavior.md`](../architecture/liveview/behavior.md).
- **Skills console:** eng contract in [`../architecture/guisaxs-skills.md`](../architecture/guisaxs-skills.md); package layout in [`AGENTS.md`](../../AGENTS.md).
- **Removed interactive Controller** (`autosaxs.pipeline`): [`../archive/legacy-interactive-pipeline.md`](../archive/legacy-interactive-pipeline.md).

---

## 8. Skills catalog

Per-skill purpose / inputs / outputs live in skill **docstrings** and the generated reference (`autosaxs get-docs` → `autosaxs-docs/skills_reference.md`; `autosaxs get-skills` for the Cursor skill). Do not duplicate that catalog here. Discovery: `autosaxs.skill.list_skills()`.

**Wrapper features (`skill_wrap.apply_batch`):** Batch application supports **`single_output_dir`** (default False): when True, all samples in a batch write to the same output directory; when False, one subdirectory per sample (stem from **`stem_from_keys`**). **`stem_from_keys`** specifies which input key(s) to use to derive the per-sample subdir name (e.g. `"profile"`, `"images"`, `"image"`). Output file stems are normalized: if an input stem starts with `sub_` or `int_`, that prefix is stripped before saving new data (see `_strip_sub_int_prefix` in `skill_wrap.py`).

### 8.x Skill details: `fit_distances` (GNOM)
- **Heuristic `rmax` selection (robust scan)**:
  - The skill MUST estimate a plausible `rmax` bracket from the data (a robust method is preferred over Guinier-derived estimates), then scan a coarse grid of candidate `rmax` values.
  - Candidate evaluation SHOULD incorporate basic physical/quality criteria available from GNOM outputs (e.g. positivity, tail behavior near `rmax`, smoothness/oscillation, and a GNOM-reported quality/estimate), selecting the most plausible solution.

### 8.y Skill details: `fit_sizes` (GNOM polydisperse D(R))
- **Polydisperse system selection**:
  - `shape="spheres"` → `--system=1` (default)
  - `shape="rods"` → `--system=5` and requires `rad56_nm` (cylinder radius)
  - `shape="ellipsoids"` is intentionally not supported in command-line mode (GNOM `system=2` is interactive-only in ATSAS command-line GNOM).


