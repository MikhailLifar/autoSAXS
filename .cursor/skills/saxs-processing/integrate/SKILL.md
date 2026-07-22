---
name: integrate
description: SAXS / small-angle x-ray scattering: integrate 2D SAXS images to 1D curves (q, I, sigma) using a calibrated integrator produced by `calibrate` (azimuthal integration; q-space).
catalog-hidden: true
---

# `autosaxs integrate` (subskill)

## Critical: `autosaxs` is a Python package

**Do not assume `autosaxs` is an ordinary system command.** It is installed **into a Python environment** (for example via `pip install autosaxs`). Pip installs a launcher script in that environment’s `bin/` directory (next to `python`, `pip`, etc.). **Always run the CLI via that launcher** — especially use an explicit path when the active shell might be the wrong interpreter.

**Preferred invocation (explicit, unambiguous):**

```bash
/path/to/myenv/bin/autosaxs integrate ...
```

If the correct environment is activated so its `bin/` is on `PATH`, the same command is:

```bash
autosaxs integrate ...
```

**What does not work:** `python -m autosaxs …` — the package has no top-level `__main__.py`. Do not try to substitute other `-m` module paths here; **use `<env>/bin/autosaxs` instead.**

If you see **`autosaxs: command not found`** (or similar), the agent **must not** treat this as a broken skill: call **`/path/to/the/environment/bin/autosaxs`** (resolve the env where `autosaxs` is installed). Never invent a fake `autosaxs` binary path.

## What I do

This skill wraps the `autosaxs integrate` CLI command / `autosaxs.skill.integrate` Python entry point.

## When to use me

- You want to run `autosaxs integrate` on real data.

## Required inputs

See the docstring section **Arguments** below.

## Procedure

1. Prepare input paths and choose an `output_dir` (if applicable).
2. If this skill requires a config file (look for a required argument like `config_path` / `config` in **Arguments** below) and you do not have one yet, generate the default config into your working directory:

```bash
/path/to/myenv/bin/autosaxs get-default-config -o /path/to/directory
```

Then use the created `config_base.conf` (or a copy of it) as the config input path and edit it if your setup requires changes.
3. Run **`/path/to/myenv/bin/autosaxs integrate …`** (or `autosaxs integrate …` when the right env is active), or call the Python function.
4. Use the returned/written output paths.

## Output requirements

See the docstring section **Returns** below.

## Tooling rules

- **`autosaxs` is always tied to a Python environment** — see **Critical: `autosaxs` is a Python package** above before running anything.
- When in doubt (CI, fresh terminals, mixed conda/system shells), **always use the full path:** **`<path-to-env>/bin/autosaxs integrate …`**.
- If you know the correct env is active on `PATH`, **`autosaxs integrate …`** is fine.
- If the skill requires a config path (e.g. `config_path` / `config`) and no config file exists yet, run **`autosaxs get-default-config -o <dir>`** to materialize the bundled default config (`config_base.conf`) into a real file, then pass that path to the skill.
- Prefer the Python API (`autosaxs.skill.integrate`) for scripting or tight integration inside Python.

## Autosaxs skill docstring

SAXS / small-angle x-ray scattering: integrate 2D SAXS images to 1D curves (q, I, sigma) using a calibrated integrator produced by `calibrate` (azimuthal integration; q-space).

### Arguments

- `images` (str): Image path expression. Can be:
  - a single `.tif` file path
  - a directory (expands to `*.tif`, non-recursive)
  - a glob expression
  - a comma-separated list of file paths (e.g. from multi-file drag & drop)
- `integrator_dir` (str): Path to the calibrated integrator directory (from `calibrate`).
- `output_dir` (str, default `.`): Directory where integrated curves are written.
- `npt` (int, default `1000`): Number of points in the output q grid.
- `use_cache` (bool, default `False`): Enable/disable caching for this skill run.
- `validation_png` (bool, default `False`): If `True`, write a PNG next to each integrated curve showing the source image (log-intensity) with integrator-masked pixels highlighted in semi-transparent red.

### Short parameter list

- npt: Number of integrated points, default: 1000
- validation_png: Show validation image

### Returns

`dict[str, str | list[str]]` with:

- `integrated_1d`: List of paths to integrated 1D `.dat` curves (one per input image).
- `validation_png` (only when `validation_png=True`): List of paths to validation PNG(s), one per input image.

### Python usage

```python
from autosaxs.skill import integrate

out = integrate(
    images="/data/sample_*.tif",
    integrator_dir="calibration/integrator",
    output_dir="integration",
    npt=1000,
    use_cache=False,
)

print(out["integrated_1d"])
```

### CLI usage

```bash
autosaxs integrate "/data/sample_01.tif, /data/sample_02.tif" calibration/integrator       --output-dir integration --npt 1000
```
