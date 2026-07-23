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

This procedure wraps the `autosaxs fit-distances` CLI command / `autosaxs.skill.fit_distances` Python entry point.

## When to use me

- You want to run `autosaxs fit-distances` on SAXS data.

## Required inputs

See the docstring section **Arguments** below.

## Procedure

1. Prepare input paths and choose an `output_dir` (if applicable).
2. Run **`/path/to/myenv/bin/autosaxs fit-distances …`** (or `autosaxs fit-distances …` when the right env is active), or call the Python function.
3. Use the returned/written output paths.

## Output requirements

See the docstring section **Returns** below.

## Tooling rules

- **`autosaxs` is always tied to a Python environment** — see **Critical: `autosaxs` is a Python package** above before running anything.
- When in doubt (CI, fresh terminals, mixed conda/system shells), **always use the full path:** **`<path-to-env>/bin/autosaxs fit-distances …`**.
- If you know the correct env is active on `PATH`, **`autosaxs fit-distances …`** is fine.
- Prefer the Python API (`autosaxs.skill.fit_distances`) for scripting or tight integration inside Python.

## Autosaxs skill docstring

SAXS / small-angle x-ray scattering: run ATSAS DATGNOM to obtain a pair distance distribution function \(p(r)\) for a monodisperse system from a 1D SAXS curve (real-space distance distribution).

### Arguments

- `profile` (str): 1D path expression (file/directory/glob). Directories expand to `*.dat` (non-recursive).
- `output_dir` (str, default `.`): Directory where the outputs are written (one subdirectory per input profile).
- `rg_nm` (float | None, default `None`): Expected Rg in nm, usually passed from Guinier analysis. If omitted, in-process Guinier analysis (`fit_guinier`) is run for an Rg span, then 1D Rg optimization in `[0, 1.5 × rg_max]` (30 s max) takes place.
- `first` (int | None, default `None`): DATGNOM `--first` (1-based point index). If omitted, taken from the low-q end of the Guinier interval from `fit_guinier`.
- `last` (int | None, default `None`): DATGNOM `--last`. If omitted, `--last` is not passed to DATGNOM.
- `smooth` (float | None, default `None`): DATGNOM `--smooth`. If omitted, defaults to `2.0`.
- `use_cache` (bool, default `False`): Enable/disable caching for this skill run.

### Returns

`dict[str, str | list[str]]` with:

- `output_subdir`: The per-sample output directory used for this profile.
- `gnom_out_paths`: List of DATGNOM `.out` paths written for this profile (typically a single “best” `.out`).
- `best_gnom_out_path`: Path to the selected “best” DATGNOM `.out`.
- `fit_distances_log_path`: Path to the extended run log YAML (`{base}_fit_distances_log.yml`) — candidates, ensemble rows, quality, failures.
- `fit_params_path`: Path to a YAML file containing the fit parameters used for the final run.
- `best_symlink_out_path`: Best-effort symlink path to the selected `.out` (may be missing on some filesystems).
- `fits_csv_path`: Path to a CSV containing candidate scores/metadata.
- `fit_vs_exp_png_path`: Fit-vs-experiment plot output.
- `fit_vs_exp_png_error`: Error message if fit-vs-experiment plot failed.
- `best_pr_png_path`: \(p(r)\) plot output or error message.
- `best_pr_png_error`: Error message if \(p(r)\) plot failed.
- `ensemble_dir`: Close-fits Dmax ensemble directory.
- `ensemble_summary_path`: Close-fits Dmax ensemble CSV summary.
- `close_fit_out_paths`: Saved GNOM `.out` paths for Dmax±10% close fits.
- `force_zero_off_out_path`: Saved GNOM `.out` with `--force-zero-rmax=N` at Dmax.
- `dmax_nm`: Maximum real-space size D_max (nm) from the selected GNOM/DATGNOM fit.
- `rg_pr_nm`: Integral Rg from p(r) (GNOM-reported or computed from the distribution).
- `i0_pr`: Integral I(0) from p(r).
- `rg_guinier_nm`: Guinier Rg (nm) from in-process `fit_guinier` or user `rg_nm`.
- `q_min_fit_nm`: Low-q bound (nm⁻¹) used in the GNOM fit (from the `.out` angular range when available).
- `total_estimate`: GNOM Total Estimate of the selected fit.
- `delta_rg_pct`: \|Rg_Guinier − Rg_P(r)\| / Rg_Guinier × 100.
- `shannon_s_min`: Minimum Shannon sampling value.
- `shannon_class`: Shannon classification.
- `shannon_ok`: Boolean indicating acceptable Shannon sampling.
- `shannon_tip`: Shannon interpretation guide.
- `pr_quality_class`: `high_quality` \| `acceptable` \| `failed`.
- `overall_status`: `HIGH QUALITY` \| `ACCEPTABLE` \| `FAILED` (quality passport label).
- `quality_rationale`: List explaining the quality assessment.
- `user_tips`: List of user tips about fit or data.
- `quality_passport_path`: YAML path with the full quality block.

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
autosaxs fit_distances subtracted/sub_sample_01.dat --output-dir distances/
autosaxs fit_distances subtracted/sub_sample_01.dat --rg-nm 10.0 --first 10 --last 100 --smooth 2.0 -o distances/
```
