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

This procedure wraps the `autosaxs model-dr-mc` CLI command / `autosaxs.skill.model_dr_mc` Python entry point.

## When to use me

- You want to run `autosaxs model-dr-mc` on SAXS data.

## Required inputs

See the docstring section **Arguments** below.

## Procedure

1. Prepare input paths and choose an `output_dir` (if applicable).
2. Run **`/path/to/myenv/bin/autosaxs model-dr-mc …`** (or `autosaxs model-dr-mc …` when the right env is active), or call the Python function.
3. Use the returned/written output paths.

## Output requirements

See the docstring section **Returns** below.

## Tooling rules

- **`autosaxs` is always tied to a Python environment** — see **Critical: `autosaxs` is a Python package** above before running anything.
- When in doubt (CI, fresh terminals, mixed conda/system shells), **always use the full path:** **`<path-to-env>/bin/autosaxs model-dr-mc …`**.
- If you know the correct env is active on `PATH`, **`autosaxs model-dr-mc …`** is fine.
- Prefer the Python API (`autosaxs.skill.model_dr_mc`) for scripting or tight integration inside Python.

## Autosaxs skill docstring

SAXS / small-angle x-ray scattering: recover a form-free volume-weighted size distribution
\(D(R)\) with per-bin uncertainties using McSAS3 Monte Carlo fitting.

### Arguments

- `profile` (str): 1D path expression (file/directory/glob). Directories expand to `*.dat` (non-recursive).
- `output_dir` (str, default `.`): Directory where the outputs are written (one subdirectory per profile).
- `config_path` (str | None, default `None`): Deprecated. YAML/config with a `model_dr_mc` section. When omitted, bundled defaults apply.
- `q_min_nm` / `q_max_nm` (float | None): Optional q bounds (nm^-1) for the fit window.
- `n_rep` (int, default `5`): Independent MC repetitions. Mean \(D(R)\) and per-bin \(\sigma\) come from this ensemble; use 50–100 for publication. Defaults to 5.
- `n_contrib` (int, default `300`): Number of sphere contributions in each MC model. Defaults to 300.
- `conv_crit` (float, default `1`): Reduced-\(\chi^2\) convergence target. Raise if experimental \(\sigma_I\) are too optimistic and runs never finish. Defaults to 1.
- `n_cores` (int, default `0`): Parallel workers for repetitions (`0` = autodetect). Defaults to 0.
- `nbins` (int, default `100`): Rebin count for input \(I(q)\) before fitting. Defaults to 100.
- `n_bin` (int, default `50`): Number of bins in the post-fit log-\(R\) volume-weighted histogram. Defaults to 50.
- `max_iter` (int, default `20000`): Max MC iterations per repetition. Defaults to 20000.
- `sld` / `sld_solvent` (float): Scattering-length densities for absolute scaling (`1e-6 Å^-2`). Relative \(I(q)\) still yields a useful relative \(D(R)\). Defaults to 33.4 and 0.0 respectively.
- `use_cache` (bool, default `False`): Enable/disable caching for this skill run.

### Short parameter list

- q_min_nm: start of fit region
- q_max_nm: end of fit region
- n_rep: number of independent MC repetitions; default: 5 (use 50–100 for publication)
- n_contrib: number of sphere contributions per model; default: 300
- conv_crit: reduced-chi2 convergence target; default: 1
- n_cores: parallel workers for repetitions; default: 0 (autodetect)
- nbins: rebin count for input I(q); default: 100
- n_bin: number of D(R) histogram bins; default: 50
- max_iter: max MC iterations per repetition; default: 20000
- sld: particle scattering-length density (1e-6 Å^-2); default: 33.4
- sld_solvent: solvent scattering-length density (1e-6 Å^-2); default: 0.0

### Returns

`dict` with:

- `output_subdir`: Per-sample output directory.
- `state_path`: McSAS3 HDF5/NeXus state (`.nxs`).
- `dr_csv_path`: CSV of \(R\), \(dR\), \(D\), \(D_\mathrm{std}\).
- `stats_path`: YAML with gof, modes, peaks, resolved limits.
- `handoff_path`: Compact YAML hints for `model_mixture`.
- `fit_png_path` / `dr_png_path`: Fit and D(R) plot paths.
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
autosaxs model-dr-mc subtracted/sub_sample_01.dat --output-dir mcsas/ 
autosaxs model-dr-mc subtracted/sub_sample_01.dat --q-min-nm 0.1 --q-max-nm 5.0 --n-rep 10 -o mcsas/
```
