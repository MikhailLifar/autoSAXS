# `autosaxs process-monodisperse` (subskill)

## Critical: `autosaxs` is a Python package

**Do not assume `autosaxs` is an ordinary system command.** It is installed **into a Python environment** (for example via `pip install autosaxs`). Pip installs a launcher script in that environment’s `bin/` directory (next to `python`, `pip`, etc.). **Always run the CLI via that launcher** — especially use an explicit path when the active shell might be the wrong interpreter.

**Preferred invocation (explicit, unambiguous):**

```bash
/path/to/myenv/bin/autosaxs process-monodisperse ...
```

If the correct environment is activated so its `bin/` is on `PATH`, the same command is:

```bash
autosaxs process-monodisperse ...
```

**What does not work:** `python -m autosaxs …` — the package has no top-level `__main__.py`. Do not try to substitute other `-m` module paths here; **use `<env>/bin/autosaxs` instead.**

If you see **`autosaxs: command not found`** (or similar), the agent **must not** treat this as a broken skill: call **`/path/to/the/environment/bin/autosaxs`** (resolve the env where `autosaxs` is installed). Never invent a fake `autosaxs` binary path.

## What I do

This procedure wraps the `autosaxs process-monodisperse` CLI command / `autosaxs.skill.process_monodisperse` Python entry point.

## When to use me

- You want to run `autosaxs process-monodisperse` on SAXS data.

## Required inputs

See the docstring section **Arguments** below.

## Procedure

1. Prepare input paths and choose an `output_dir` (if applicable).
2. Run **`/path/to/myenv/bin/autosaxs process-monodisperse …`** (or `autosaxs process-monodisperse …` when the right env is active), or call the Python function.
3. Use the returned/written output paths.

## Output requirements

See the docstring section **Returns** below.

## Tooling rules

- **`autosaxs` is always tied to a Python environment** — see **Critical: `autosaxs` is a Python package** above before running anything.
- When in doubt (CI, fresh terminals, mixed conda/system shells), **always use the full path:** **`<path-to-env>/bin/autosaxs process-monodisperse …`**.
- If you know the correct env is active on `PATH`, **`autosaxs process-monodisperse …`** is fine.
- Prefer the Python API (`autosaxs.skill.process_monodisperse`) for scripting or tight integration inside Python.

## Autosaxs skill docstring

SAXS / small-angle x-ray scattering: run the monodisperse single-profile quality pipeline
(Guinier → dimensionless Kratky → DATGNOM p(r) / Shannon–ΔRg passport → optional DAMMIF
when quality gates pass → per-sample PDF report).

**First-arg inference:** if ``profile`` is a directory containing ``.tif``/``.tiff``
**and** a pipeline config is available (``config.conf`` in that dir, or
``config_path`` / ``--conf``), run the TIFF full pipeline (calibrate → integrate →
average as needed → pair/subtract via ``buffer_rules``) then analyze each
profile (subtracted when paired; unsubtracted when RHS is ``""``). Unmatched
curves are skipped. Otherwise treat ``profile`` as subtracted ``.dat``
path(s) (backward compatible). TIFF dir without config → clear
``FileNotFoundError`` (not silent fallthrough). Explicit folder+config
router: ``process_full`` (reads ``analysis: mono|poly``).

### Arguments

- `profile` (str): Subtracted 1D path expression (file/directory/glob of `*.dat`),
  **or** a TIFF frames directory (see inference above). Directories expand
  non-recursively for ``.dat`` mode.
- `output_dir` (str, default `.`): Pipeline root; leaf skills write under subdirectories here.
- `config_path` (str | None, default `None`): Optional YAML config forwarded to leaf skills
  (and required for TIFF full-pipeline inference when ``<dir>/config.conf`` is absent).
- `first` / `last` (int | None): Optional fixed Guinier interval (1-based); both required together.
  Guinier `first` is forwarded to DATGNOM; Guinier `last` is **not** passed to DATGNOM
  (window too narrow for p(r)).
- `smooth` (float | None, default `None`): Optional DATGNOM `--smooth` for `fit_distances`.
- `n_runs` (int, default `5`): DAMMIF replica count for `model_dam` when the quality gate passes.
- `use_cache` (bool, default `False`): Forwarded to leaf skills.

### Returns

`dict` with:

- `report_pdf_path`: Primary PDF quality passport (when written).
- `assembled_report_md_path`: Merged Markdown report.
- `pipeline_dir`: The `output_dir` used as the pipeline root.
- `basename`: Sample basename used for report assembly.
- `model_dam_ran`: Whether `model_dam` was invoked.
- `model_dam_skip_reason`: Why DAMMIF was skipped (empty when run).
- `fit_guinier`: Return dict from `fit_guinier`.
- `analyze_kratky`: Return dict from `analyze_kratky`.
- `fit_distances`: Return dict from `fit_distances`.
- `model_dam`: Return dict from `model_dam` (empty dict when skipped).
- `report_individual`: Return dict from `report_individual`.
- `tiff_front` (only when TIFF full pipeline ran): TIFF front-end return dict.

### Python usage

```python
from autosaxs.skill import process_monodisperse

out = process_monodisperse(
    profile="subtracted/sub_sample_01.dat",
    output_dir="mono_out",
)
print(out["report_pdf_path"])

# TIFF directory + config.conf (mono analysis after subtract):
out = process_monodisperse("/data/run01", output_dir="mono_out")
```

### CLI usage

```bash
autosaxs process-monodisperse subtracted/sub_sample_01.dat --output-dir mono_out
autosaxs process-monodisperse /data/run01 --conf /data/run01/config.conf -o mono_out
# or explicit folder+config router (reads analysis: mono|poly):
autosaxs process-full /data/run01 --conf /data/run01/config.conf -o mono_out
```
