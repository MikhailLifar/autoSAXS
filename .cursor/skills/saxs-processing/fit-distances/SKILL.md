---
name: fit-distances
description: SAXS / small-angle x-ray scattering: run ATSAS DATGNOM to obtain a pair distance distribution function \(p(r)\) for a monodisperse system from a 1D SAXS curve (real-space distance distribution).
catalog-hidden: true
---

# `autosaxs fit-distances` (subskill)

## Critical: `autosaxs` is a Python package

**Do not assume `autosaxs` is an ordinary system command.** It is installed **into a Python environment** (for example via `pip install autosaxs`). Pip installs a launcher script in that environment’s `bin/` directory (next to `python`, `pip`, etc.). **Always run the CLI via that launcher** — especially use an explicit path when the active shell might be the wrong interpreter.

**Preferred invocation (explicit, unambiguous):**

```bash
/path/to/myenv/bin/autosaxs fit-distances ...
```

If the correct environment is activated so its `bin/` is on `PATH`, the same command is:

```bash
autosaxs fit-distances ...
```

**What does not work:** `python -m autosaxs …` — the package has no top-level `__main__.py`. Do not try to substitute other `-m` module paths here; **use `<env>/bin/autosaxs` instead.**

If you see **`autosaxs: command not found`** (or similar), the agent **must not** treat this as a broken skill: call **`/path/to/the/environment/bin/autosaxs`** (resolve the env where `autosaxs` is installed). Never invent a fake `autosaxs` binary path.

## What I do

This skill wraps the `autosaxs fit-distances` CLI command / `autosaxs.skill.fit_distances` Python entry point.

## When to use me

- You want to run `autosaxs fit-distances` on real data.

## Required inputs

See the docstring section **Arguments** below.

## Procedure

1. Prepare input paths and choose an `output_dir` (if applicable).
2. If this skill requires a config file (look for a required argument like `config_path` / `config` in **Arguments** below) and you do not have one yet, generate the default config into your working directory:

```bash
/path/to/myenv/bin/autosaxs get-default-config -o /path/to/directory
```

Then use the created `config_base.conf` (or a copy of it) as the config input path and edit it if your setup requires changes.
3. Run **`/path/to/myenv/bin/autosaxs fit-distances …`** (or `autosaxs fit-distances …` when the right env is active), or call the Python function.
4. Use the returned/written output paths.

## Output requirements

See the docstring section **Returns** below.

## Tooling rules

- **`autosaxs` is always tied to a Python environment** — see **Critical: `autosaxs` is a Python package** above before running anything.
- When in doubt (CI, fresh terminals, mixed conda/system shells), **always use the full path:** **`<path-to-env>/bin/autosaxs fit-distances …`**.
- If you know the correct env is active on `PATH`, **`autosaxs fit-distances …`** is fine.
- If the skill requires a config path (e.g. `config_path` / `config`) and no config file exists yet, run **`autosaxs get-default-config -o <dir>`** to materialize the bundled default config (`config_base.conf`) into a real file, then pass that path to the skill.
- Prefer the Python API (`autosaxs.skill.fit_distances`) for scripting or tight integration inside Python.

## Autosaxs skill docstring

SAXS / small-angle x-ray scattering: run ATSAS DATGNOM to obtain a pair distance distribution function \(p(r)\) for a monodisperse system from a 1D SAXS curve (real-space distance distribution).

### Arguments

- `profile` (str): 1D path expression (file/dir/glob). Directories expand to `*.dat` (non-recursive).
- `output_dir` (str, default `.`): Directory where the GNOM outputs are written (one subdirectory per input profile).
- `rg_nm` (float | None, default `None`): Expected Rg in nm. If omitted, taken from AUTORG when possible, else from Guinier search.
- `first` (int | None, default `None`): DATGNOM `--first`. If omitted, taken from AUTORG Guinier interval when possible. If set with `last`, runs one fit. If set alone, `last` is auto-searched unless AUTORG succeeded and `last` is omitted (then DATGNOM runs without `--last`). If omitted and AUTORG fails or gives no interval, `first` is auto-searched.
- `last` (int | None, default `None`): DATGNOM `--last`. Same pairing rules as `first`; if set alone, `first` is auto-searched. Omitted with successful AUTORG implies a single DATGNOM run without `--last`.
- `smooth` (float | None, default `None`): DATGNOM `--smooth`. If set, that value is used and smoothness is not searched. If omitted during auto-search, trials use smoothness `2.0`. In full manual mode (`first` and `last` both set), omitted means do not pass `--smooth`.
- `use_cache` (bool, default `False`): Enable/disable caching for this skill run.

### Returns

`dict[str, str | list[str]]` with:

- `output_subdir`: The per-sample output directory used for this profile.
- `gnom_out_paths`: List of DATGNOM `.out` paths written for this profile (typically a single “best” `.out`).
- `best_gnom_out_path`: Path to the selected “best” DATGNOM `.out`.
- `best_summary_path`: Path to a YAML summary of candidate runs and the selected parameters.
- `fit_params_path`: Path to a YAML file containing the fit parameters used for the final run.
- `best_symlink_out_path`: Best-effort symlink path to the selected `.out` (may be missing on some filesystems).
- `fits_csv_path`: Path to a CSV containing candidate scores/metadata.
- `fit_vs_exp_png_path` / `fit_vs_exp_png_error`: Fit-vs-experiment plot output or error message.
- `best_pr_png_path` / `best_pr_png_error`: \(p(r)\) plot output or error message.

### Python usage

```python
from autosaxs.skill import fit_distances

out = fit_distances(
    profile="subtracted/sub_sample_01.dat",
    output_dir="distances",
    use_cache=False,
)

print(out["best_gnom_out_path"])
```

### CLI usage

```bash
autosaxs fit_distances subtracted/sub_sample_01.dat --output-dir distances
```
