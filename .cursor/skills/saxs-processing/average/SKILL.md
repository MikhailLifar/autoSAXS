---
name: average
description: SAXS / small-angle x-ray scattering: radiation-damage-aware averaging of per-frame 1D curves.
catalog-hidden: true
---

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

This skill wraps the `autosaxs average` CLI command / `autosaxs.skill.average` Python entry point.

## When to use me

- You want to run `autosaxs average` on real data.

## Required inputs

See the docstring section **Arguments** below.

## Procedure

1. Prepare input paths and choose an `output_dir` (if applicable).
2. If this skill requires a config file (look for a required argument like `config_path` / `config` in **Arguments** below) and you do not have one yet, generate the default config into your working directory:

```bash
/path/to/myenv/bin/autosaxs get-default-config -o /path/to/directory
```

Then use the created `config_base.conf` (or a copy of it) as the config input path and edit it if your setup requires changes.
3. Run **`/path/to/myenv/bin/autosaxs average …`** (or `autosaxs average …` when the right env is active), or call the Python function.
4. Use the returned/written output paths.

## Output requirements

See the docstring section **Returns** below.

## Tooling rules

- **`autosaxs` is always tied to a Python environment** — see **Critical: `autosaxs` is a Python package** above before running anything.
- When in doubt (CI, fresh terminals, mixed conda/system shells), **always use the full path:** **`<path-to-env>/bin/autosaxs average …`**.
- If you know the correct env is active on `PATH`, **`autosaxs average …`** is fine.
- If the skill requires a config path (e.g. `config_path` / `config`) and no config file exists yet, run **`autosaxs get-default-config -o <dir>`** to materialize the bundled default config (`config_base.conf`) into a real file, then pass that path to the skill.
- Prefer the Python API (`autosaxs.skill.average`) for scripting or tight integration inside Python.

## Autosaxs skill docstring

SAXS / small-angle x-ray scattering: radiation-damage-aware averaging of per-frame 1D curves.

Expands a path expression to sorted per-frame ``.dat`` files, compares each frame to the
lexicographically first reference (CorMap + reduced chi-squared), truncates at the first
rejection, and writes an inverse-variance weighted merge.

### Arguments

- `profiles` (str): 1D path expression (file / directory / glob / comma-list). Directories expand
  to ``*.dat`` (non-recursive). Files are sorted lexicographically.
- `output_dir` (str, default ``./averaged``): Directory for the averaged curve, frame-selection
  CSV, and report fragments.
- `cormap_p_min` (float, default ``0.05``): CorMap p-value threshold for borderline warnings.
- `chi2_max` (float, default ``1.25``): Reject frame (and stop) when reduced chi-squared vs
  reference exceeds this value.
- `chi2_min` (float, default ``0.9``): Warn when reduced chi-squared is below this value.
- `use_cache` (bool, default ``False``): Enable/disable caching for this skill run.

### Returns

``dict[str, str]`` with:

- `averaged_1d`: Path to the merged ``int_<prefix>.dat`` curve.
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
