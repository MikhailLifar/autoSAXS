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

This procedure wraps the `autosaxs calibrate` CLI command / `autosaxs.skill.calibrate` Python entry point.

## When to use me

- You want to run `autosaxs calibrate` on SAXS data.

## Required inputs

See the docstring section **Arguments** below.

## Procedure

1. Prepare input paths and choose an `output_dir` (if applicable).
2. Run **`/path/to/myenv/bin/autosaxs calibrate …`** (or `autosaxs calibrate …` when the right env is active), or call the Python function.
3. Use the returned/written output paths.

## Output requirements

See the docstring section **Returns** below.

## Tooling rules

- **`autosaxs` is always tied to a Python environment** — see **Critical: `autosaxs` is a Python package** above before running anything.
- When in doubt (CI, fresh terminals, mixed conda/system shells), **always use the full path:** **`<path-to-env>/bin/autosaxs calibrate …`**.
- If you know the correct env is active on `PATH`, **`autosaxs calibrate …`** is fine.
- Prefer the Python API (`autosaxs.skill.calibrate`) for scripting or tight integration inside Python.

## Autosaxs skill docstring

SAXS / small-angle x-ray scattering: calibrate detector geometry using calibrant image. This is a prerequisite for `integrate` (azimuthal integration).

### Arguments

- `calibrant_image` (str): Path to the calibrant image (e.g. TIFF).
- `output_dir` (str, default `.`): Directory where results are written.
- `config_path` (str | None, default `None`): Depricated. Path to a YAML config file with a `calibrate` section. When omitted, bundled defaults are used.
- `mask` (str | None, default `None`): Optional user detector pixel mask (`.txt` / `.npy` / `.msk`). When omitted, the automatic mask alone becomes the effective mask. When provided, it is OR-combined once with the automatic mask. The user mask file is never overwritten. Results are written as `effective_mask.npy` and `auto_mask.npy` **alongside** `integrator/` (not inside it).
- `mask_mode` (str | None, default `None`): Deprecated compatibility selector (`f`/`from_file`, `a`/`auto`, `c`/`combined`). Effective mask is always `auto | optional user mask`; this flag only records intent for configs/GUIs. Defaults to `a`/`auto` when no user mask is given, else `c`/`combined`.
- `calibrant` (str | None, default `None`): Calibrant name (must be in `pyFAI.calibrant.ALL_CALIBRANTS`). Defaults to `AgBh`.
- `wavelength` (float | None, default `None`): X-ray wavelength in **Ångström**. Defaults to 1.445 Å.
- `dist_guess` (float | None, default `None`): Optional initial sample–detector distance in **metres** passed to pyFAI before geometry refinement. When omitted, distance is estimated from the innermost calibrant ring. Usually works well if not set.
- `use_cache` (bool, default `False`): Enable/disable caching for this skill run.

Notes:

- Automatic mask always includes the beam-stop disk and all negative-intensity pixels. Local IQR outlier masking is off by default; enable via `mask_config.calc_abnormal_mask` in config.
- `integrator/` stores geometry only. Both `effective_mask.npy` and `auto_mask.npy` are always written next to it (even when no user mask was provided). Later `integrate --mask` replaces the effective mask entirely (no further OR).

### Short parameter list

- mask: Optional user mask; default: automatic mask only.
- mask_mode: Deprecated; default: auto (or combined when a user mask is set).
- calibrant: name of the calibrant, default: AgBh.
- wavelength: X-ray wavelength in Ångström, default: 1.445 Å.
- dist_guess: Optional: initial sample-detector distance in metres (algorithm works good if this is not set).

### Returns

`dict[str, str]` with these output path roles:

- `integrator_dir`: Directory containing calibrated geometry (used by `integrate`).
- `effective_mask_path`: Path to `effective_mask.npy` alongside `integrator/`.
- `auto_mask_path`: Path to `auto_mask.npy` alongside `integrator/`.
- `refined_path`: Path to the refined detector geometry YAML (PONI params plus Fit2D `center_y_px` / `center_x_px`).
- `calibration_plots_dir`: Directory containing calibration plots.
- `calibration_curve_plot_path`: Path to the calibrantion q/I curve plot (PNG).
- `calibration_curve_dat_path`: Path to the calibrantion q/I curve (`.dat`, same format as integrated 1D curves).
- `calibration_mask_path`: Path to the detector pixel mask visualization (PNG).

### Python usage

```python
from autosaxs.skill import calibrate

out = calibrate(
    calibrant_image="AgBh.tif",
    output_dir="calibration/",
    mask="mask.msk",  # optional
    use_cache=False,
)

print(out["integrator_dir"])
print(out["refined_path"])
```

### CLI usage

```bash
autosaxs calibrate AgBh.tif --output-dir calibration
autosaxs calibrate AgBh.tif --output-dir calibration --mask mask.msk
autosaxs calibrate AgBh.tif --conf my_config.conf -o calibration/
```
