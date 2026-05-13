---
name: fit-mixture
description: SAXS / small-angle x-ray scattering: run MIXTURE fits on a 1D subtracted curve, select the best model by BIC, and write a comparison plot, size distribution plot, and results CSV (mixture / multi-population size distributions).
catalog-hidden: true
---

# `autosaxs fit-mixture` (subskill)

## Critical: `autosaxs` is a Python package

**Do not assume `autosaxs` is an ordinary system command.** It is installed **into a Python environment** (for example via `pip install autosaxs`). Pip installs a launcher script in that environment’s `bin/` directory (next to `python`, `pip`, etc.). **Always run the CLI via that launcher** — especially use an explicit path when the active shell might be the wrong interpreter.

**Preferred invocation (explicit, unambiguous):**

```bash
/path/to/myenv/bin/autosaxs fit-mixture ...
```

If the correct environment is activated so its `bin/` is on `PATH`, the same command is:

```bash
autosaxs fit-mixture ...
```

**What does not work:** `python -m autosaxs …` — the package has no top-level `__main__.py`. Do not try to substitute other `-m` module paths here; **use `<env>/bin/autosaxs` instead.**

If you see **`autosaxs: command not found`** (or similar), the agent **must not** treat this as a broken skill: call **`/path/to/the/environment/bin/autosaxs`** (resolve the env where `autosaxs` is installed). Never invent a fake `autosaxs` binary path.

## What I do

This skill wraps the `autosaxs fit-mixture` CLI command / `autosaxs.skill.fit_mixture` Python entry point.

## When to use me

- You want to run `autosaxs fit-mixture` on real data.

## Required inputs

See the docstring section **Arguments** below.

## Procedure

1. Prepare input paths and choose an `output_dir` (if applicable).
2. If this skill requires a config file (look for a required argument like `config_path` / `config` in **Arguments** below) and you do not have one yet, generate the default config into your working directory:

```bash
/path/to/myenv/bin/autosaxs get-default-config -o /path/to/directory
```

Then use the created `config_base.conf` (or a copy of it) as the config input path and edit it if your setup requires changes.
3. Run **`/path/to/myenv/bin/autosaxs fit-mixture …`** (or `autosaxs fit-mixture …` when the right env is active), or call the Python function.
4. Use the returned/written output paths.

## Output requirements

See the docstring section **Returns** below.

## Tooling rules

- **`autosaxs` is always tied to a Python environment** — see **Critical: `autosaxs` is a Python package** above before running anything.
- When in doubt (CI, fresh terminals, mixed conda/system shells), **always use the full path:** **`<path-to-env>/bin/autosaxs fit-mixture …`**.
- If you know the correct env is active on `PATH`, **`autosaxs fit-mixture …`** is fine.
- If the skill requires a config path (e.g. `config_path` / `config`) and no config file exists yet, run **`autosaxs get-default-config -o <dir>`** to materialize the bundled default config (`config_base.conf`) into a real file, then pass that path to the skill.
- Prefer the Python API (`autosaxs.skill.fit_mixture`) for scripting or tight integration inside Python.

## Autosaxs skill docstring

SAXS / small-angle x-ray scattering: run MIXTURE fits on a 1D subtracted curve, select the best model by BIC, and write a comparison plot, size distribution plot, and results CSV (mixture / multi-population size distributions).

Prerequisites:

- Requires the ATSAS `mixture` executable to be available on `PATH` (this skill shells out to `mixture`).

### Arguments

- `profile` (str): 1D path expression (file/dir/glob). Directories expand to `*.dat` (non-recursive).
- `output_dir` (str, default `.`): Directory where the MIXTURE outputs are written.
- `config_path` (str | None, default `None`): Path to the autosaxs YAML config (must include a `mixture` section). Required for this skill.
- `q_min_nm` (float | None, default `None`): Optional q minimum bound (nm^-1) for the fitting range.
- `q_max_nm` (float | None, default `None`): Optional q maximum bound (nm^-1) for the fitting range.
- `use_cache` (bool, default `False`): Enable/disable caching for this skill run.

Important constraint:

- If you set `q_max_nm`, you must also set `q_min_nm` (otherwise the skill raises `ValueError`).

### Returns

`dict[str, str]` with:

- `output_subdir`: The subdirectory that contains MIXTURE outputs.
- `comparison_path`: Path to the MIXTURE comparison plot (linear y).
- `comparison_log_path`: Path to the MIXTURE comparison plot (log y).
- `distributions_path`: Path to the MIXTURE size distributions plot.
- `results_csv_path`: Path to the MIXTURE results CSV.

### Python usage

```python
from autosaxs.skill import fit_mixture

out = fit_mixture(
    profile="subtracted/sub_sample_01.dat",
    output_dir="mixture",
    config_path="config_autosaxs.yml",
    q_min_nm=0.8,
    q_max_nm=2.5,
    use_cache=False,
)

print(out["results_csv_path"])
```

### CLI usage

```bash
autosaxs fit-mixture subtracted/sub_sample_01.dat --output-dir mixture --config-path config_autosaxs.yml       --q-min-nm 0.8 --q-max-nm 2.5
```
