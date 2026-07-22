---
name: model-dr-mc
description: SAXS / small-angle x-ray scattering: recover a form-free volume-weighted size distribution
catalog-hidden: true
---

# `autosaxs model-dr-mc` (subskill)

## Critical: `autosaxs` is a Python package

**Do not assume `autosaxs` is an ordinary system command.** It is installed **into a Python environment** (for example via `pip install autosaxs`). Pip installs a launcher script in that environment’s `bin/` directory (next to `python`, `pip`, etc.). **Always run the CLI via that launcher** — especially use an explicit path when the active shell might be the wrong interpreter.

**Preferred invocation (explicit, unambiguous):**

```bash
/path/to/myenv/bin/autosaxs model-dr-mc ...
```

If the correct environment is activated so its `bin/` is on `PATH`, the same command is:

```bash
autosaxs model-dr-mc ...
```

**What does not work:** `python -m autosaxs …` — the package has no top-level `__main__.py`. Do not try to substitute other `-m` module paths here; **use `<env>/bin/autosaxs` instead.**

If you see **`autosaxs: command not found`** (or similar), the agent **must not** treat this as a broken skill: call **`/path/to/the/environment/bin/autosaxs`** (resolve the env where `autosaxs` is installed). Never invent a fake `autosaxs` binary path.

## What I do

This skill wraps the `autosaxs model-dr-mc` CLI command / `autosaxs.skill.model_dr_mc` Python entry point.

## When to use me

- You want to run `autosaxs model-dr-mc` on real data.

## Required inputs

See the docstring section **Arguments** below.

## Procedure

1. Prepare input paths and choose an `output_dir` (if applicable).
2. If this skill requires a config file (look for a required argument like `config_path` / `config` in **Arguments** below) and you do not have one yet, generate the default config into your working directory:

```bash
/path/to/myenv/bin/autosaxs get-default-config -o /path/to/directory
```

Then use the created `config_base.conf` (or a copy of it) as the config input path and edit it if your setup requires changes.
3. Run **`/path/to/myenv/bin/autosaxs model-dr-mc …`** (or `autosaxs model-dr-mc …` when the right env is active), or call the Python function.
4. Use the returned/written output paths.

## Output requirements

See the docstring section **Returns** below.

## Tooling rules

- **`autosaxs` is always tied to a Python environment** — see **Critical: `autosaxs` is a Python package** above before running anything.
- When in doubt (CI, fresh terminals, mixed conda/system shells), **always use the full path:** **`<path-to-env>/bin/autosaxs model-dr-mc …`**.
- If you know the correct env is active on `PATH`, **`autosaxs model-dr-mc …`** is fine.
- If the skill requires a config path (e.g. `config_path` / `config`) and no config file exists yet, run **`autosaxs get-default-config -o <dir>`** to materialize the bundled default config (`config_base.conf`) into a real file, then pass that path to the skill.
- Prefer the Python API (`autosaxs.skill.model_dr_mc`) for scripting or tight integration inside Python.

## Autosaxs skill docstring

SAXS / small-angle x-ray scattering: recover a form-free volume-weighted size distribution
\(D(R)\) with per-bin uncertainties using McSAS3 Monte Carlo fitting (polydisperse spheres).

Fits an ensemble of independent sphere-contribution models to a subtracted 1D curve, then
histograms the recovered radii. Bin heights are volume-weighted; error bars are the sample
standard deviation across independent repetitions. For publication-quality uncertainty on
\(D(R)\), raise ``n_rep`` to 50–100 (default 5 is for interactive / pipeline use).

Prerequisites:

- Python package ``mcsas3`` (installed with autosaxs).
- Sphere form factor only in this skill (McSAS3 internal ``mcsas_sphere``).

### Arguments

- `profile` (str): 1D path expression (file/dir/glob). Directories expand to `*.dat` (non-recursive).
- `output_dir` (str, default `.`): Directory where McSAS outputs are written (one subdirectory per profile).
- `config_path` (str | None, default `None`): Optional YAML/config with a `model_dr_mc` section. When omitted, bundled defaults apply.
- `q_min_nm` / `q_max_nm` (float | None): Optional q bounds (nm^-1) for the fit window.
- `n_rep` (int, default `5`): Independent MC repetitions. Mean \(D(R)\) and per-bin \(\sigma\) come from this ensemble; use 50–100 for publication.
- `n_contrib` (int, default `300`): Number of sphere contributions in each MC model.
- `conv_crit` (float, default `1`): Reduced-\(\chi^2\) convergence target. Raise if experimental \(\sigma_I\) are too optimistic and runs never finish.
- `n_cores` (int, default `0`): Parallel workers for repetitions (`0` = autodetect).
- `nbins` (int, default `100`): Rebin count for input \(I(q)\) before fitting.
- `n_bin` (int, default `50`): Number of bins in the post-fit log-\(R\) volume-weighted histogram.
- `max_iter` (int, default `20000`): Max MC iterations per repetition.
- `sld` / `sld_solvent` (float): Scattering-length densities for absolute scaling (`1e-6 Å^-2`). Relative \(I(q)\) still yields a useful relative \(D(R)\).
- `use_cache` (bool, default `False`): Enable/disable caching for this skill run.

Important constraint:

- If you set `q_max_nm`, you must also set `q_min_nm` (otherwise the skill raises `ValueError`).

### Returns

`dict` with:

- `output_subdir`: Per-sample output directory.
- `state_path`: McSAS3 HDF5/NeXus state (`.nxs`).
- `dr_csv_path`: CSV of \(R\), \(dR\), \(D\), \(D_\mathrm{std}\).
- `stats_path`: YAML with gof, modes, peaks, resolved limits.
- `handoff_path`: Compact YAML hints for `model_mixture`.
- `fit_png_path` / `dr_png_path` / `result_card_png_path`: Plot paths (result card may be empty on McPlot failure).
- `n_rep`, `r_min_nm`, `r_max_nm`, `q_min_nm`, `q_max_nm`, `n_components_suggested`.

### Python usage

```python
from autosaxs.skill import model_dr_mc

out = model_dr_mc(
    profile="subtracted/sub_sample_01.dat",
    output_dir="mcsas",
    n_rep=5,
    use_cache=False,
)
print(out["dr_png_path"])
```

### CLI usage

```bash
autosaxs model-dr-mc subtracted/sub_sample_01.dat --output-dir mcsas --n-rep 10
```
