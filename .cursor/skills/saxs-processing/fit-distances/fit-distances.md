# `autosaxs fit-distances` (subskill)

Ordinary procedure doc for the orchestrator — **not** a Cursor Agent Skill (`SKILL.md`). Open this file when the plan reaches this step.

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
- `rg_nm` (float | None, default `None`): Expected Rg in nm. If omitted, run in-process Guinier analysis (`fit_guinier`) for an Rg span, then 1D optimize Rg in `[0, 1.5 × rg_max]` (30 s max) scoring each DATGNOM trial as Total Estimate − neg_frac.
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
- `fit_vs_exp_png_path` / `fit_vs_exp_png_error`: Fit-vs-experiment plot output or error message.
- `best_pr_png_path` / `best_pr_png_error`: \(p(r)\) plot output or error message.
- `ensemble_dir` / `ensemble_summary_path`: Close-fits Dmax ensemble directory and CSV summary.
- `close_fit_out_paths`: Saved GNOM `.out` paths for Dmax±10% close fits.
- `force_zero_off_out_path`: Saved GNOM `.out` with `--force-zero-rmax=N` at Dmax.
- `dmax_nm`: Maximum real-space size D_max (nm) from the selected GNOM/DATGNOM fit.
- `rg_pr_nm` / `i0_pr`: Integral Rg and I(0) from p(r) (GNOM-reported or computed from the distribution).
- `rg_guinier_nm`: Guinier Rg (nm) from in-process `fit_guinier` or user `rg_nm`.
- `q_min_fit_nm`: Low-q bound (nm⁻¹) used in the GNOM fit (from the `.out` angular range when available).
- `total_estimate`: GNOM Total Estimate of the selected fit.
- `delta_rg_pct`: \|Rg_Guinier − Rg_P(r)\| / Rg_Guinier × 100.
- `shannon_s_min`, `shannon_class`, `shannon_ok`, `shannon_tip`: Shannon sampling metrics and interpretation guide.
- `pr_quality_class`: `high_quality` \| `acceptable` \| `failed`.
- `overall_status`: `HIGH QUALITY` \| `ACCEPTABLE` \| `FAILED` (quality passport label).
- `quality_rationale` / `user_tips`: Lists explaining the quality assessment.
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
autosaxs fit_distances subtracted/sub_sample_01.dat --output-dir distances
```
