# `autosaxs process-polydisperse` (subskill)

## Critical: `autosaxs` is a Python package

**Do not assume `autosaxs` is an ordinary system command.** It is installed **into a Python environment** (for example via `pip install autosaxs`). Pip installs a launcher script in that environment’s `bin/` directory (next to `python`, `pip`, etc.). **Always run the CLI via that launcher** — especially use an explicit path when the active shell might be the wrong interpreter.

**Preferred invocation (explicit, unambiguous):**

```bash
/path/to/myenv/bin/autosaxs process-polydisperse ...
```

If the correct environment is activated so its `bin/` is on `PATH`, the same command is:

```bash
autosaxs process-polydisperse ...
```

**What does not work:** `python -m autosaxs …` — the package has no top-level `__main__.py`. Do not try to substitute other `-m` module paths here; **use `<env>/bin/autosaxs` instead.**

If you see **`autosaxs: command not found`** (or similar), the agent **must not** treat this as a broken skill: call **`/path/to/the/environment/bin/autosaxs`** (resolve the env where `autosaxs` is installed). Never invent a fake `autosaxs` binary path.

## What I do

This procedure wraps the `autosaxs process-polydisperse` CLI command / `autosaxs.skill.process_polydisperse` Python entry point.

## When to use me

- You want to run `autosaxs process-polydisperse` on SAXS data.

## Required inputs

See the docstring section **Arguments** below.

## Procedure

1. Prepare input paths and choose an `output_dir` (if applicable).
2. Run **`/path/to/myenv/bin/autosaxs process-polydisperse …`** (or `autosaxs process-polydisperse …` when the right env is active), or call the Python function.
3. Use the returned/written output paths.

## Output requirements

See the docstring section **Returns** below.

## Tooling rules

- **`autosaxs` is always tied to a Python environment** — see **Critical: `autosaxs` is a Python package** above before running anything.
- When in doubt (CI, fresh terminals, mixed conda/system shells), **always use the full path:** **`<path-to-env>/bin/autosaxs process-polydisperse …`**.
- If you know the correct env is active on `PATH`, **`autosaxs process-polydisperse …`** is fine.
- Prefer the Python API (`autosaxs.skill.process_polydisperse`) for scripting or tight integration inside Python.

## Autosaxs skill docstring

SAXS / small-angle x-ray scattering: run the polydisperse single-profile quality pipeline
(Guinier → GNOM D(R) / size-distribution passport → optional MIXTURE when requested and
D(R) quality gates pass → per-sample PDF report).

**First-arg inference:** if ``profile`` is a directory containing ``.tif``/``.tiff``
**and** a pipeline config is available (``config.conf`` in that dir, or
``config_path`` / ``--conf``), run the TIFF full pipeline (calibrate → integrate →
average as needed → subtract via ``buffer_rules``) then analyze each subtracted
``.dat``. Otherwise treat ``profile`` as subtracted ``.dat`` path(s) (backward
compatible). TIFF dir without config → clear ``FileNotFoundError`` (not silent
fallthrough). Explicit folder+config router: ``process_full`` (reads
``analysis: mono|poly``).

### Arguments

- `profile` (str): Subtracted 1D path expression (file/directory/glob of `*.dat`),
  **or** a TIFF frames directory (see inference above). Directories expand
  non-recursively for ``.dat`` mode.
- `output_dir` (str, default `.`): Pipeline root; leaf skills write under subdirectories here.
- `config_path` (str | None, default `None`): Optional YAML config forwarded to leaf skills
  (and required for TIFF full-pipeline inference when ``<dir>/config.conf`` is absent).
- `first` / `last` (int | None): Optional fixed Guinier interval (1-based); both required together.
  Guinier `first` is forwarded to `fit_sizes` when set (or when auto-Guinier succeeds); Guinier
  `last` is **not** passed to GNOM D(R) (same narrow-window rule as mono DATGNOM).
- `shape` (str, default `spheres`): Forwarded to `fit_sizes` (currently spheres).
- `q_min` / `q_max` (float | None): Optional `fit_sizes` q bounds (nm⁻¹). When set, they override
  Guinier-first handoff for the corresponding end (do not combine `q_min` with an implicit first).
- `run_mixture` (bool, default `False`): When `True`, run `model_mixture` if D(R) is high quality.
  Default stays off to match liveview auto / real-data light (MIXTURE is interactive Confirm in GUI).
- `use_cache` (bool, default `False`): Forwarded to leaf skills.

### Returns

`dict` with:

- `report_pdf_path`: Primary PDF quality passport (when written).
- `assembled_report_md_path`: Merged Markdown report.
- `pipeline_dir`: The `output_dir` used as the pipeline root.
- `basename`: Sample basename used for report assembly.
- `model_mixture_ran`: Whether `model_mixture` was invoked.
- `model_mixture_skip_reason`: Why MIXTURE was skipped (empty when run).
- `fit_guinier`: Return dict from `fit_guinier`.
- `fit_sizes`: Return dict from `fit_sizes`.
- `model_mixture`: Return dict from `model_mixture` (empty dict when skipped).
- `report_individual`: Return dict from `report_individual`.
- `tiff_front` (only when TIFF full pipeline ran): TIFF front-end return dict.

### Python usage

```python
from autosaxs.skill import process_polydisperse

out = process_polydisperse(
    profile="subtracted/sub_sample_01.dat",
    output_dir="poly_out",
)
print(out["report_pdf_path"])

# TIFF directory + config.conf (poly analysis after subtract):
out = process_polydisperse("/data/run01", output_dir="poly_out")
```

### CLI usage

```bash
autosaxs process-polydisperse subtracted/sub_sample_01.dat --output-dir poly_out
autosaxs process-polydisperse /data/run01 --conf /data/run01/config.conf -o poly_out
# or explicit folder+config router (reads analysis: mono|poly):
autosaxs process-full /data/run01 --conf /data/run01/config.conf -o poly_out
```
