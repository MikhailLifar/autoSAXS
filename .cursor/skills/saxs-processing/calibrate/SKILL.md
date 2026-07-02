---
name: calibrate
description: SAXS / small-angle x-ray scattering: calibrate detector geometry using a calibration image and a config (ring-analysis + geometry refinement). This is a prerequisite for `integrate` (azimuthal integration).
catalog-hidden: true
---

# `autosaxs calibrate` (subskill)

## Critical: `autosaxs` is a Python package

**Do not assume `autosaxs` is an ordinary system command.** It is installed **into a Python environment** (for example via `pip install autosaxs`). Pip installs a launcher script in that environment’s `bin/` directory (next to `python`, `pip`, etc.). **Always run the CLI via that launcher** — especially use an explicit path when the active shell might be the wrong interpreter.

**Preferred invocation (explicit, unambiguous):**

```bash
/path/to/myenv/bin/autosaxs calibrate ...
```

If the correct environment is activated so its `bin/` is on `PATH`, the same command is:

```bash
autosaxs calibrate ...
```

**What does not work:** `python -m autosaxs …` — the package has no top-level `__main__.py`. Do not try to substitute other `-m` module paths here; **use `<env>/bin/autosaxs` instead.**

If you see **`autosaxs: command not found`** (or similar), the agent **must not** treat this as a broken skill: call **`/path/to/the/environment/bin/autosaxs`** (resolve the env where `autosaxs` is installed). Never invent a fake `autosaxs` binary path.

## What I do

This skill wraps the `autosaxs calibrate` CLI command / `autosaxs.skill.calibrate` Python entry point.

## When to use me

- You want to run `autosaxs calibrate` on real data.

## Required inputs

See the docstring section **Arguments** below.

## Procedure

1. Prepare input paths and choose an `output_dir` (if applicable).
2. If this skill requires a config file (look for a required argument like `config_path` / `config` in **Arguments** below) and you do not have one yet, generate the default config into your working directory:

```bash
/path/to/myenv/bin/autosaxs get-default-config -o /path/to/directory
```

Then use the created `config_base.conf` (or a copy of it) as the config input path and edit it if your setup requires changes.
3. Run **`/path/to/myenv/bin/autosaxs calibrate …`** (or `autosaxs calibrate …` when the right env is active), or call the Python function.
4. Use the returned/written output paths.

## Output requirements

See the docstring section **Returns** below.

## Tooling rules

- **`autosaxs` is always tied to a Python environment** — see **Critical: `autosaxs` is a Python package** above before running anything.
- When in doubt (CI, fresh terminals, mixed conda/system shells), **always use the full path:** **`<path-to-env>/bin/autosaxs calibrate …`**.
- If you know the correct env is active on `PATH`, **`autosaxs calibrate …`** is fine.
- If the skill requires a config path (e.g. `config_path` / `config`) and no config file exists yet, run **`autosaxs get-default-config -o <dir>`** to materialize the bundled default config (`config_base.conf`) into a real file, then pass that path to the skill.
- Prefer the Python API (`autosaxs.skill.calibrate`) for scripting or tight integration inside Python.

## Autosaxs skill docstring

SAXS / small-angle x-ray scattering: calibrate detector geometry using a calibration image and a config (ring-analysis + geometry refinement). This is a prerequisite for `integrate` (azimuthal integration).

### Arguments

- `calib_image` (str): Path to the calibration image (e.g. TIFF) used for ring analysis.
- `output_dir` (str, default `.`): Directory where results are written.
- `config_path` (str | None, default `None`): Optional path to a YAML config file with a `calibrate` section. When omitted, bundled defaults from the installed `autosaxs` package are used.
- `mask` (str | None, default `None`): Optional path to a mask used during ring analysis. Supports .txt (NuPy format), .msk (Fit2d)
- `mask_mode` (str | None, default `None`): Mask mode selector (`f`/`from_file`, `a`/`auto`, `c`/`combined`). Defaults come from config when omitted.
- `calibrant` (str | None, default `None`): Calibrant name (must be in `pyFAI.calibrant.ALL_CALIBRANTS`). Defaults come from config when omitted.
- `wavelength` (float | None, default `None`): X-ray wavelength in **Ångström**. Defaults come from config when omitted.
- `dist_guess` (float | None, default `None`): Optional initial sample–detector distance in **metres** passed to pyFAI before geometry refinement. When omitted, distance is estimated from the innermost calibration ring.
- `use_cache` (bool, default `False`): Enable/disable caching for this skill run.

Important constraints:

- If `mask_mode` is `f/from_file` or `c/combined`, `mask` **must** be provided (the skill raises `ValueError` otherwise).

### Returns

`dict[str, str]` with these output path roles:

- `integrator_dir`: Directory containing the calibrated integrator (used by `integrate`).
- `refined_path`: Path to the refined calibration YAML.
- `calibration_plots_dir`: Directory containing calibration plots.
- `calibration_curve_plot_path`: Path to the calibration q/I curve plot (PNG).
- `calibration_curve_dat_path`: Path to the calibration q/I curve (`.dat`, same format as integrated 1D curves).
- `calibration_mask_path`: Path to the calibration mask visualization (PNG).

### Python usage

```python
from autosaxs.skill import calibrate

out = calibrate(
    calib_image="AgBh.tif",
    output_dir="calibration",
    mask="mask.msk",
    mask_mode="f",
    use_cache=False,
)

print(out["integrator_dir"])
print(out["refined_path"])
```

### CLI usage

```bash
autosaxs calibrate AgBh.tif --output-dir calibration --mask mask.msk
autosaxs calibrate AgBh.tif --conf my_config.conf --output-dir calibration
```
