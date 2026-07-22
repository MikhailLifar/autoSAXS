# AGENTS.md — navigation cache for this repo

**Purpose:** structural and architectural notes so agents do not re-discover the codebase each session.  
**Not** a user manual — see `README.md`, `docs/`, and `autosaxs get-readme`.  
**Maintain this file:** update when you add/move modules, change architecture, or learn a non-obvious convention.

---

## Workspace layout

| Path | What it is |
|------|------------|
| `/home/mikl/KurchatovCoop/repos/` | **Git repo root** — Python package, tests, docs, `.cursor/` |
| `/home/mikl/KurchatovCoop/` | **Parent workspace** — validation data, sample TIFFs, one-off scripts, experiment outputs |
| `/home/mikl/KurchatovCoop/.cursor` | Symlink → `repos/.cursor` |

Real-data tests expect validation fixtures under **`/home/mikl/KurchatovCoop/validation/`** (setup: `repos/scripts/setup_validation_data.py`).

---

## Packages at a glance

Single distribution (`pyproject.toml`, version in `[project].version`). Four console entry points:

| Entry point | Package | Status | Role |
|-------------|---------|--------|------|
| `autosaxs` | `autosaxs/` | **active** | Core SAXS processing + CLI |
| `guisaxs-skills` | `guisaxs_skills/` | **active** | PyQt5 skill console |
| `guisaxs-liveview` | `guisaxs_liveview/` | **active** | Thin launcher → `guisaxs_skills.liveview` |
| `guisaxs` | `guisaxs/` | **missing** | Legacy CustomTkinter app; entry point registered but package absent from tree |

GUI extras: `pip install -e repos[gui]` (adds `customtkinter`, `tkinterdnd2`, `PyQt5`, `watchdog`).

---

## Architecture (high level)

```mermaid
flowchart TB
  subgraph interfaces [Interfaces]
    CLI["autosaxs CLI\nautosaxs/cli/cli.py"]
    SkillsGUI["guisaxs-skills\nPyQt5"]
    Liveview["guisaxs-liveview\nwatch-folder"]
    Pipeline["pipeline/\nController + EventBus"]
  end

  subgraph core_pkg [autosaxs package]
    Skill["autosaxs.skill\nlist_skills()"]
    Core["autosaxs.core\nI/O, PathExpression, plots"]
    Wrap["skill/skill_wrap.py\ncache + batch"]
  end

  subgraph external [External]
    PyFAI["pyFAI"]
    ATSAS["ATSAS 3.2.1\nPATH"]
  end

  CLI --> Skill
  SkillsGUI -->|"subprocess:\npython -m autosaxs.cli.cli"| CLI
  Liveview --> SkillsGUI
  Pipeline --> Skill
  Skill --> Core
  Skill --> Wrap
  Skill --> PyFAI
  Skill --> ATSAS
```

**Paradigm:** processing = **skills** (pure functions returning output path dicts). Pipelines are composed outside the package (scripts, liveview executor, legacy `Controller`). Spec: `docs/skills_paradigm.md`.

**GUI rule:** PyQt apps **do not** call skill functions in-process for execution. They introspect `autosaxs.skill` for metadata, then run `python -m autosaxs.cli.cli <skill> ...` via `guisaxs_skills/logic/runner_qprocess.py` (`SkillRunner`).

---

## `autosaxs/` — where is what

```
autosaxs/
├── skill/          # PUBLIC API — one callable per processing step
├── core/           # Low-level algorithms, I/O, PathExpression (no skill imports)
├── cli/            # argparse CLI; subcommands generated from skill signatures
├── pipeline/       # Legacy interactive multi-step workflow (EventBus + Controller)
├── foreign/        # Vendored supervised_ml, aiAssistantFramework
└── resources/      # Bundled config, prompts, help HTML, AI skill templates
```

### `autosaxs/skill/` — skills registry

- **Discovery:** `autosaxs.skill.list_skills()` — single source of truth for CLI and GUI.
- **Order:** `SKILL_ORDER` in `skill/__init__.py`.
- **Layout:** mostly one module per skill; subpackages for heavy skills (`calibrate/`, `fit_guinier/`, `model_mixture/`).
- **Infrastructure (not skills):** `skill_wrap.py` (cache + `@apply_batch`), `common.py` (path coercion), `config.py` (merge bundled + user config), `deps.py` (internal import hub).

| Skill | Module | Notes |
|-------|--------|-------|
| calibrate | `skill/calibrate/` | Ring analysis + geometry refinement; requires mask |
| integrate | `skill/integrate.py` | 2D→1D via saved integrator |
| average | `skill/average.py` | CorMap frame selection |
| integrate_proxy | `skill/integrate_proxy.py` | Quick-look without calibration |
| subtract | `skill/subtract.py` | Buffer subtraction |
| plot | `skill/plot.py` | Guinier / Kratky / log-log |
| plot_2d | `skill/plot_2d.py` | 2D detector PNGs |
| fit_guinier | `skill/fit_guinier/` | Adaptive Guinier region |
| analyze_kratky | `skill/analyze_kratky.py` | Dimensionless Kratky conformation analysis |
| fit_distances | `skill/fit_distances.py` | DATGNOM monodisperse p(r) |
| fit_sizes | `skill/fit_sizes.py` | GNOM polydisperse D(R) |
| model_mixture | `skill/model_mixture/` | ATSAS MIXTURE (`fit_mixture` deprecated alias) |
| model_bodies | `skill/model_bodies.py` | ATSAS BODIES (`fit_bodies` deprecated alias) |
| model_dam | `skill/model_dam.py` | DAMMIF ab initio (+ DAMAVER when n_runs>1) |
| model_density | `skill/model_density/` | DENSS continuous density |
| report_individual | `skill/report_individual.py` | Per-sample PDF from fragments |
| report_summary | `skill/report_summary.py` | Pipeline summary PDF |

**Adding a skill:** register in `_SKILL_IMPORTS` (`skill/__init__.py`), add to `SKILL_ORDER`, extend `tests/test_skill.py`.

### `autosaxs/core/` — primitives

| Module | Look here for… |
|--------|----------------|
| `path_expression.py` | Typed path/glob/comma-list expansion (`Dat`, `Tiff`, `Mask`, …) |
| `utils.py` | `read_saxs`, `write_saxs`, `load_config`, detector helpers, `LATEST_STEPS_PATH` |
| `integrator.py` | `IntegratorExtended` (pyFAI wrapper) |
| `guinier.py` | Pure Guinier math |
| `gnom.py` | GNOM `.out` parsing, candidate scoring |
| `pddf.py` | p(r) from BODIES/DAMMIF shapes |
| `viewer.py` | Matplotlib plotting (`PLTViewer`) |
| `report_fragments.py` | Decentralized `*_report_individual.md` / `*_report_summary.yaml` |
| `event_bus.py` | `EventBus`, `EventType` (pipeline + optional skill progress) |
| `context.py` | `Context` — working dir, config |

### `autosaxs/cli/`

- Entry: `autosaxs.cli:main` → `cli/cli.py`.
- Subcommands: auto-built from `list_skills()` signatures (`--kebab-case` options, `--cache`/`--no-cache`, `--conf`).
- Agent helpers: `get-readme`, `get-skills`, `get-default-config` (see `resources/agent_quickstart.txt`).
- **Invoke as:** `python -m autosaxs.cli.cli` (no `__main__` on `autosaxs.cli`).

### `autosaxs/pipeline/` — legacy interactive orchestration

| Module | Role |
|--------|------|
| `saxs_controller.py` | `Controller` — event-driven step sequencer |
| `cli_interface.py` | Stdin prompts ↔ EventBus |
| `gui_interface.py` | CustomTkinter dialogs ↔ EventBus |
| `api.py` | `fast_first_processing` script API |

Spec: `docs/pipeline_interactive_spec.md`. New work should prefer skills + scripts, not extending Controller unless explicitly needed.

### `autosaxs/resources/`

| Path | Contents |
|------|----------|
| `config_base.conf` | Bundled skill-keyed YAML defaults (`calibrate:`, `subtract:`, …) |
| `agent_quickstart.txt` | CLI epilog for agents |
| `help/guisaxs_liveview/` | Bundled HTML help (manifest + pages) |
| `ai_skills/`, `prompts/` | LLM / assistant templates |
| `readme/` | Source for generated README |

---

## GUI packages

### `guisaxs_skills/` — main GUI codebase

```
guisaxs_skills/
├── app.py, __main__.py     # guisaxs-skills entry
├── core/                   # SkillMeta, RunRequest, paths, settings, event_bus
├── logic/                  # skill_catalog, runner_qprocess, smart_defaults, …
├── ui/                     # main_window, skill_form, style, path_field, previews
└── liveview/               # watch-folder app (also used by guisaxs-liveview)
    ├── app.py, window.py   # entry + main window shell
    ├── controller/         # LiveviewController + handlers (history, ingest, session)
    ├── pipeline/           # LiveviewJobExecutor, jobs, queue
    ├── ingest/             # TIFF watchers, stability, tiff_revision
    ├── session/            # state, persistence, output_paths, workdir
    ├── services/           # pure logic (artifacts, calibration, history, skills)
    └── ui/
        ├── panels/         # left, middle, right/
        ├── wizards/        # calibration, buffer, mask, fit, subtraction
        └── widgets/        # plots, viewer_3d
```

**Key modules:**

| Module | Role |
|--------|------|
| `logic/skill_catalog.py` | `discover_skills()` from `autosaxs.skill` → `SkillMeta` |
| `ui/skill_form.py` | Dynamic form from `SkillMeta`; emits `RunRequest` |
| `ui/style.py` | **Canonical** PyQt theme/colors (`COLOR_MUTED_TEXT`, `apply_style`) |
| `ui/path_field.py` | Path input with DnD |
| `logic/runner_qprocess.py` | `SkillRunner` — subprocess CLI, streams logs |
| `logic/autosaxs_cli.py` | Blocking `get-default-config` helper |
| `liveview/pipeline/executor.py` | **Active** liveview orchestrator (`LiveviewJobExecutor`) |
| `liveview/session/state.py` | Session states A → B → BD → C → CD |
| `liveview/ingest/watcher.py` | watchdog TIFF detection |

**Liveview note:** orchestration in `liveview/controller/` (`LiveviewController` + handlers); skill execution via `liveview/pipeline/`; package `__init__.py` files re-export common symbols for shorter imports.

### `guisaxs_liveview/`

Thin package: `__main__.py` → `guisaxs_skills.liveview.app.run_liveview_app()`.  
Help assets live in `autosaxs/resources/help/guisaxs_liveview/`.

---

## “Where do I find…?” quick lookup

| Task | Start here |
|------|------------|
| Add/modify a processing step | `autosaxs/skill/`, `skill/__init__.py`, `tests/test_skill.py` |
| CLI argument parsing | `autosaxs/cli/cli.py` (`_add_skill_subparser`) |
| Path expansion rules | `autosaxs/core/path_expression.py`, `skill/common.py` |
| Caching (`.cache` YAML) | `autosaxs/skill/skill_wrap.py`, `docs/skills_paradigm.md` §2.1 |
| Config merge precedence | `autosaxs/skill/config.py`, `resources/config_base.conf` |
| Subtraction algorithm | `autosaxs/skill/subtract.py`, `autosaxs/tests/test_subtract.py` |
| Guinier / GNOM / ATSAS fits | `skill/fit_guinier/`, `fit_distances.py`, `fit_sizes.py`, `gnom_fit_common.py` |
| Report assembly | `autosaxs/core/report_fragments.py`, `skill/report_*.py` |
| GUI skill metadata | `guisaxs_skills/logic/skill_catalog.py` |
| GUI subprocess runner | `guisaxs_skills/logic/runner_qprocess.py` |
| Liveview job building | `guisaxs_skills/liveview/pipeline/` (`executor.py`, `jobs.py`) |
| PyQt colors/theme | `guisaxs_skills/ui/style.py` |
| Bundled defaults export | `autosaxs get-default-config -o <dir>` |
| Skill docstrings → Cursor skills | `autosaxs get-skills -o <dir>` |

---

## Conventions (remember)

### Python environment

Use conda env **`dev_autosaxs`**:

- Python: `/home/mikl/.conda/envs/dev_autosaxs/bin/python`
- pip: `/home/mikl/.conda/envs/dev_autosaxs/bin/pip`

`helpers/run_tests.sh` uses these paths.

### ATSAS

`import autosaxs` checks ATSAS **3.2.1** via `dammif -v` on PATH. Missing/wrong version → `RuntimeError`.

### Skills contract

- Return `dict[str, str | list[str]]` — stable output role keys (tests enforce).
- Docstrings = CLI help + `get-skills` export source.
- `use_cache=False` by default; CLI `--cache` enables `.cache` in output dir.
- Path expressions: file, dir (non-recursive `*.tif` / `*.dat`), glob, comma-list; empty expansion = error.

### Config merge

`merge_skill_params`: **kwargs > user `--conf` section > bundled `config_base.conf`**.

### Reuse before adding

Per `.cursor/rules/reuse-existing-functionality.mdc`:

- Theme/colors → `guisaxs_skills/ui/style.py`
- Form widgets → `skill_form.py`, `path_field.py`
- Skill metadata → `skill_catalog.py`

No parallel APIs or duplicated literals.

### Tests

| Location | What |
|----------|------|
| `repos/tests/test_skill.py` | **Primary** — every skill's contract, cache, return keys |
| `repos/tests/test_skills_real_data.py` | E2E vs `validation/` reference data |
| `repos/tests/test_guisaxs_liveview.py` | Liveview GUI (needs xvfb + `[gui]`) |
| `repos/autosaxs/tests/` | Focused unit tests (subtract, average, guinier, pddf, report_fragments) |
| `repos/guisaxs_skills/tests/` | smart_defaults, session_persistence, calibration_display |

Run all (CI order): `repos/helpers/run_tests.sh`.

**Stale:** `README.md` references `repos/tests/test_guisaxs.py` — file not present.

### Docs & specs

| Doc | Topic |
|-----|-------|
| `README.md` | User-facing skills reference (long) |
| `docs/skills_paradigm.md` | Skills architecture spec |
| `docs/guisaxs_skills_spec.md` | Skills GUI spec |
| `docs/guisaxs_liveview_spec.md` | Liveview spec |
| `docs/pipeline_interactive_spec.md` | Legacy pipeline / EventBus |
| `docs/guisaxs_spec.md` | Legacy guisaxs (package missing) |

### Cursor project files

- Rules: `repos/.cursor/rules/` (`python-interpreter`, `reuse-existing-functionality`, `agent-suggestions`)
- SAXS workflow skills: `repos/.cursor/skills/saxs-processing/` (also exportable via `autosaxs get-skills`)
- Agent suggestion inbox: `repos/.cursor/suggestions/`

---

## Install & dev commands

```bash
cd /home/mikl/KurchatovCoop/repos
/home/mikl/.conda/envs/dev_autosaxs/bin/pip install -e ".[gui]"
/home/mikl/.conda/envs/dev_autosaxs/bin/autosaxs --help
/home/mikl/.conda/envs/dev_autosaxs/bin/python -m guisaxs_skills      # skills console
/home/mikl/.conda/envs/dev_autosaxs/bin/python -m guisaxs_liveview     # liveview
```

Headless GUI tests: `xvfb-run -a python -m pytest tests/test_guisaxs_liveview.py`

---

## Known gaps / stale references

- `guisaxs` package and `tests/test_guisaxs.py` referenced in README but absent.
- README says `python -m autosaxs.cli`; code uses `python -m autosaxs.cli.cli`.

---

*Last structured pass: 2026-07-06. Update this file when you touch architecture or discover a better "start here" path.*
