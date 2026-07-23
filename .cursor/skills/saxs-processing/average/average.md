# `autosaxs average` (subskill)

## Critical: `autosaxs` is a Python package

**Do not assume `autosaxs` is an ordinary system command.** It is installed **into a Python environment** (for example via `pip install autosaxs`). Pip installs a launcher script in that environment’s `bin/` directory (next to `python`, `pip`, etc.). **Always run the CLI via that launcher** — especially use an explicit path when the active shell might be the wrong interpreter.

**Preferred invocation (explicit, unambiguous):**

```bash
/path/to/myenv/bin/autosaxs average ...
```

If the correct environment is activated so its `bin/` is on `PATH`, the same command is:

```bash
autosaxs average ...
```

**What does not work:** `python -m autosaxs …` — the package has no top-level `__main__.py`. Do not try to substitute other `-m` module paths here; **use `<env>/bin/autosaxs` instead.**

If you see **`autosaxs: command not found`** (or similar), the agent **must not** treat this as a broken skill: call **`/path/to/the/environment/bin/autosaxs`** (resolve the env where `autosaxs` is installed). Never invent a fake `autosaxs` binary path.

## What I do

This procedure wraps the `autosaxs average` CLI command / `autosaxs.skill.average` Python entry point.

## When to use me

- You want to run `autosaxs average` on SAXS data.

## Required inputs

See the docstring section **Arguments** below.

## Procedure

1. Prepare input paths and choose an `output_dir` (if applicable).
2. Run **`/path/to/myenv/bin/autosaxs average …`** (or `autosaxs average …` when the right env is active), or call the Python function.
3. Use the returned/written output paths.

## Output requirements

See the docstring section **Returns** below.

## Tooling rules

- **`autosaxs` is always tied to a Python environment** — see **Critical: `autosaxs` is a Python package** above before running anything.
- When in doubt (CI, fresh terminals, mixed conda/system shells), **always use the full path:** **`<path-to-env>/bin/autosaxs average …`**.
- If you know the correct env is active on `PATH`, **`autosaxs average …`** is fine.
- Prefer the Python API (`autosaxs.skill.average`) for scripting or tight integration inside Python.

## Autosaxs skill docstring

SAXS / small-angle x-ray scattering: radiation-damage-aware averaging of per-frame 1D SAXS curves.

### Arguments

- `profiles` (str): 1D path expression (file/directory/glob). Directories expand to `*.dat` (non-recursive). Files are sorted lexicographically.
- `output_dir` (str, default `./averaged`): Directory where the outputs are written.
- `cormap_p_min` (float, default `0.05`): CorMap p-value threshold for borderline warnings.
- `chi2_max` (float, default `1.25`): Reject frame (and stop) when reduced chi-squared vs reference exceeds this value.
- `chi2_min` (float, default `0.9`): Warn when reduced chi-squared is below this value.
- `use_cache` (bool, default `False`): Enable/disable caching for this skill run.

### Short parameter list

- cormap_p_min: internal parameter, recommended not to change, default: 0.05
- chi2_max: internal parameter, recommended not to change, default: 1.25
- chi2_min: internal parameter, recommended not to change, default: 0.9

### Returns

dict[str, str] with:

- `averaged_1d`: Path to the merged `int_<prefix>.dat` curve.
- `frame_selection_csv`: Path to per-frame selection diagnostics CSV.

### Python usage

```python
from autosaxs.skill import average

out = average(
    profiles="integrated/exp_*.dat",
    output_dir="./averaged",
    use_cache=False,
)
print(out["averaged_1d"])
```

### CLI usage

```bash
autosaxs average "integrated/exp_*.dat" --output-dir ./averaged
```
