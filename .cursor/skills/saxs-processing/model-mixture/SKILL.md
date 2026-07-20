---
name: model-mixture
description: SAXS / small-angle x-ray scattering: run MIXTURE fits on a 1D subtracted curve, select the best model by BIC, and write a comparison plot, size distribution plot, and results CSV (mixture / multi-population size distributions).
catalog-hidden: true
---

# `autosaxs model-mixture` (subskill)

## Critical: `autosaxs` is a Python package

**Do not assume `autosaxs` is an ordinary system command.** It is installed **into a Python environment** (for example via `pip install autosaxs`). Pip installs a launcher script in that environment’s `bin/` directory (next to `python`, `pip`, etc.). **Always run the CLI via that launcher** — especially use an explicit path when the active shell might be the wrong interpreter.

**Preferred invocation (explicit, unambiguous):**

```bash
/path/to/myenv/bin/autosaxs model-mixture ...
```

If the correct environment is activated so its `bin/` is on `PATH`, the same command is:

```bash
autosaxs model-mixture ...
```

**What does not work:** `python -m autosaxs …` — the package has no top-level `__main__.py`. Do not try to substitute other `-m` module paths here; **use `<env>/bin/autosaxs` instead.**

If you see **`autosaxs: command not found`** (or similar), the agent **must not** treat this as a broken skill: call **`/path/to/the/environment/bin/autosaxs`** (resolve the env where `autosaxs` is installed). Never invent a fake `autosaxs` binary path.

## What I do

This skill wraps the `autosaxs model-mixture` CLI command / `autosaxs.skill.model_mixture` Python entry point.

## When to use me

- You want to run `autosaxs model-mixture` on real data.

## Required inputs

See the docstring section **Arguments** below.

## Procedure

1. Prepare input paths and choose an `output_dir` (if applicable).
2. If this skill requires a config file (look for a required argument like `config_path` / `config` in **Arguments** below) and you do not have one yet, generate the default config into your working directory:

```bash
/path/to/myenv/bin/autosaxs get-default-config -o /path/to/directory
```

Then use the created `config_base.conf` (or a copy of it) as the config input path and edit it if your setup requires changes.
3. Run **`/path/to/myenv/bin/autosaxs model-mixture …`** (or `autosaxs model-mixture …` when the right env is active), or call the Python function.
4. Use the returned/written output paths.

## Output requirements

See the docstring section **Returns** below.

## Tooling rules

- **`autosaxs` is always tied to a Python environment** — see **Critical: `autosaxs` is a Python package** above before running anything.
- When in doubt (CI, fresh terminals, mixed conda/system shells), **always use the full path:** **`<path-to-env>/bin/autosaxs model-mixture …`**.
- If you know the correct env is active on `PATH`, **`autosaxs model-mixture …`** is fine.
- If the skill requires a config path (e.g. `config_path` / `config`) and no config file exists yet, run **`autosaxs get-default-config -o <dir>`** to materialize the bundled default config (`config_base.conf`) into a real file, then pass that path to the skill.
- Prefer the Python API (`autosaxs.skill.model_mixture`) for scripting or tight integration inside Python.

## Autosaxs skill docstring

SAXS / small-angle x-ray scattering: run MIXTURE fits on a 1D subtracted curve, select the best model by BIC, and write a comparison plot, size distribution plot, and results CSV (mixture / multi-population size distributions).

Prerequisites:

- Requires the ATSAS `mixture` executable to be available on `PATH` (this skill shells out to `mixture`).

### Arguments

- `profile` (str): 1D path expression (file/dir/glob). Directories expand to `*.dat` (non-recursive).
- `output_dir` (str, default `.`): Directory where the MIXTURE outputs are written.
- `config_path` (str | None, default `None`): Optional path to a YAML config file with a `model_mixture` section. When omitted, bundled defaults apply.
- `q_min_nm` / `q_max_nm` (float | None): Optional q bounds (nm^-1); set via CLI or user config (not in bundled template).
- `maxit`, `max_nph`: MIXTURE parameters; defaults from bundled `model_mixture` section when omitted.
- `plot_I_q` (bool, default `False`): Write I vs q fit comparison plot (labels show BIC).
- `plot_logI_logq` (bool, default `False`): Write log I vs log q fit comparison plot (labels show BIC_log).
- `plot_logI_q` (bool, default `True`): Write log I vs q fit comparison plot (labels show chi2).
- `r_min` (float | None): MIXTURE minimum radius (nm). If omitted, defaults to `0.1`. Converted to Å internally for ATSAS MIXTURE.
- `r_max` (float | None): MIXTURE maximum radius (nm). If omitted, defaults to `rmax_nm` from in-process `fit_sizes`.
- `poly_min` (float | None): MIXTURE minimum polydispersity (nm). If omitted, defaults to `0.05`.
- `poly_max` (float | None): MIXTURE maximum polydispersity (nm). If omitted, defaults to `0.5 × r_max`.
- `use_cache` (bool, default `False`): Enable/disable caching for this skill run.

Important constraint:

- If you set `q_max_nm`, you must also set `q_min_nm` (otherwise the skill raises `ValueError`).

### Returns

`dict[str, str]` with:

- `output_subdir`: The subdirectory that contains MIXTURE outputs.
- `comparison_path`: Path to the I vs q comparison plot (empty when `plot_I_q=False`).
- `comparison_loglog_path`: Path to the log I vs log q comparison plot (empty when `plot_logI_logq=False`).
- `comparison_log_path`: Path to the log I vs q comparison plot (empty when `plot_logI_q=False`).
- `distributions_path`: Path to the MIXTURE size distributions plot.
- `results_csv_path`: Path to the MIXTURE results CSV.
- `r_max_nm` / `poly_max_nm`: Resolved MIXTURE radius bounds (nm), including defaults when omitted.
- `r_min_nm` / `poly_min_nm`: Resolved MIXTURE radius/polydispersity floors (nm).

### Python usage

```python
from autosaxs.skill import model_mixture

out = model_mixture(
    profile="subtracted/sub_sample_01.dat",
    output_dir="mixture",
    q_min_nm=0.8,
    q_max_nm=2.5,
    use_cache=False,
)

print(out["results_csv_path"])
```

### CLI usage

```bash
autosaxs model-mixture subtracted/sub_sample_01.dat --output-dir mixture --q-min-nm 0.8 --q-max-nm 2.5
```
