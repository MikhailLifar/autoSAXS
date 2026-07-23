# `autosaxs integrate-proxy` (subskill)

## Critical: `autosaxs` is a Python package

**Do not assume `autosaxs` is an ordinary system command.** It is installed **into a Python environment** (for example via `pip install autosaxs`). Pip installs a launcher script in that environment’s `bin/` directory (next to `python`, `pip`, etc.). **Always run the CLI via that launcher** — especially use an explicit path when the active shell might be the wrong interpreter.

**Preferred invocation (explicit, unambiguous):**

```bash
/path/to/myenv/bin/autosaxs integrate-proxy ...
```

If the correct environment is activated so its `bin/` is on `PATH`, the same command is:

```bash
autosaxs integrate-proxy ...
```

**What does not work:** `python -m autosaxs …` — the package has no top-level `__main__.py`. Do not try to substitute other `-m` module paths here; **use `<env>/bin/autosaxs` instead.**

If you see **`autosaxs: command not found`** (or similar), the agent **must not** treat this as a broken skill: call **`/path/to/the/environment/bin/autosaxs`** (resolve the env where `autosaxs` is installed). Never invent a fake `autosaxs` binary path.

## What I do

This procedure wraps the `autosaxs integrate-proxy` CLI command / `autosaxs.skill.integrate_proxy` Python entry point.

## When to use me

- You want to run `autosaxs integrate-proxy` on SAXS data.

## Required inputs

See the docstring section **Arguments** below.

## Procedure

1. Prepare input paths and choose an `output_dir` (if applicable).
2. Run **`/path/to/myenv/bin/autosaxs integrate-proxy …`** (or `autosaxs integrate-proxy …` when the right env is active), or call the Python function.
3. Use the returned/written output paths.

## Output requirements

See the docstring section **Returns** below.

## Tooling rules

- **`autosaxs` is always tied to a Python environment** — see **Critical: `autosaxs` is a Python package** above before running anything.
- When in doubt (CI, fresh terminals, mixed conda/system shells), **always use the full path:** **`<path-to-env>/bin/autosaxs integrate-proxy …`**.
- If you know the correct env is active on `PATH`, **`autosaxs integrate-proxy …`** is fine.
- Prefer the Python API (`autosaxs.skill.integrate_proxy`) for scripting or tight integration inside Python.

## Autosaxs skill docstring

SAXS / small-angle x-ray scattering: integrate 2D TIFF image(s) to a 1D curve **without detector calibration**, using radial averaging in pixel-radius space (quick-look / debugging; not q-calibrated).

### Arguments

- `image` (str): 2D image path expression (file/directory/glob). Directories expand to `*.tif` (non-recursive).
- `output_dir` (str, default `.`): Directory where the outputs are written.
- `mask` (str | None, default `None`): Optional mask path; same shape as the image. (pyFAI convention: masked pixels are excluded.)
- `cy` (float | None, default `None`): Optional beam center y in pixels. Must be set together with `cx`. Defaults to None.
- `cx` (float | None, default `None`): Optional beam center x in pixels. Must be set together with `cy`. Defaults to None.
- `npt` (int, default `1000`): Number of points in the output x grid.
- `use_cache` (bool, default `False`): Enable/disable caching for this skill run.

Notes:

- If `cy/cx` are not provided, the skill **estimates** the center by radial-symmetry optimization and also writes a center diagnostic plot `*_center.png` into `output_dir`.
- If center estimation fails for an input, that item is skipped and the skill may return an empty list for `integrated_1d`.

### Returns

dict[str, str | list[str]] with:

- `integrated_1d`: Path (or list of paths, if `image` is a directory) to integrated 1D `.dat` curves.

### Python usage

```python
from autosaxs.skill import integrate_proxy

out = integrate_proxy(
    image="raw/sample_01.tif",
    output_dir="integration_proxy",
    mask="mask.msk",
    npt=1000,
    use_cache=False,
)

print(out["integrated_1d"])
```

### CLI usage

```bash
autosaxs integrate-proxy raw/sample_01.tif --output-dir integration_proxy --mask mask.msk --npt 1000
```
