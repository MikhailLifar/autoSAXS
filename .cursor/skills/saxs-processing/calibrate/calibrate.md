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
- `mask` (str): Path to a detector pixel mask. Supports .txt (NuPy format), .msk (Fit2d)
- `mask_mode` (str | None, default `None`): Mask mode selector (`f`/`from_file`, `a`/`auto`, `c`/`combined`). Defaults to `f`/`from_file`.
- `calibrant` (str | None, default `None`): Calibrant name (must be in `pyFAI.calibrant.ALL_CALIBRANTS`). Defaults to `AgBh`.
- `wavelength` (float | None, default `None`): X-ray wavelength in **Ångström**. Defaults to 1.445 Å.
- `dist_guess` (float | None, default `None`): Optional initial sample–detector distance in **metres** passed to pyFAI before geometry refinement. When omitted, distance is estimated from the innermost calibrant ring. Usually works well if not set.
- `use_cache` (bool, default `False`): Enable/disable caching for this skill run.

Important constraints:

- `mask` is always required by the skill and the CLI.

### Short parameter list

- mask_mode: Default: load mask from file as is.
- calibrant: name of the calibrant, default: AgBh.
- wavelength: X-ray wavelength in Ångström, default: 1.445 Å.
- dist_guess: Optional: initial sample-detector distance in metres (algorithm works good if this is not set).

### Returns

`dict[str, str]` with these output path roles:

- `integrator_dir`: Directory containing the calibrated integrator (used by `integrate`).
- `refined_path`: Path to the refined detector geometry YAML.
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
autosaxs calibrate AgBh.tif --conf my_config.conf -o calibration/
```
