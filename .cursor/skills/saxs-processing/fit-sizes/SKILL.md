---
name: fit-sizes
description: SAXS / small-angle x-ray scattering: run ATSAS GNOM (system=1/5) to obtain a size distribution function \(D(R)\) for a polydisperse system from a 1D SAXS curve (polydispersity; spheres/rods).
catalog-hidden: true
---

# `autosaxs fit-sizes` (subskill)

## Critical: `autosaxs` is a Python package

**Do not assume `autosaxs` is an ordinary system command.** It is installed **into a Python environment** (for example via `pip install autosaxs`). Pip installs a launcher script in that environment’s `bin/` directory (next to `python`, `pip`, etc.). **Always run the CLI via that launcher** — especially use an explicit path when the active shell might be the wrong interpreter.

**Preferred invocation (explicit, unambiguous):**

```bash
/path/to/myenv/bin/autosaxs fit-sizes ...
```

If the correct environment is activated so its `bin/` is on `PATH`, the same command is:

```bash
autosaxs fit-sizes ...
```

**What does not work:** `python -m autosaxs …` — the package has no top-level `__main__.py`. Do not try to substitute other `-m` module paths here; **use `<env>/bin/autosaxs` instead.**

If you see **`autosaxs: command not found`** (or similar), the agent **must not** treat this as a broken skill: call **`/path/to/the/environment/bin/autosaxs`** (resolve the env where `autosaxs` is installed). Never invent a fake `autosaxs` binary path.

## What I do

This skill wraps the `autosaxs fit-sizes` CLI command / `autosaxs.skill.fit_sizes` Python entry point.

## When to use me

- You want to run `autosaxs fit-sizes` on real data.

## Required inputs

See the docstring section **Arguments** below.

## Procedure

1. Prepare input paths and choose an `output_dir` (if applicable).
2. If this skill requires a config file (look for a required argument like `config_path` / `config` in **Arguments** below) and you do not have one yet, generate the default config into your working directory:

```bash
/path/to/myenv/bin/autosaxs get-default-config -o /path/to/directory
```

Then use the created `config_base.conf` (or a copy of it) as the config input path and edit it if your setup requires changes.
3. Run **`/path/to/myenv/bin/autosaxs fit-sizes …`** (or `autosaxs fit-sizes …` when the right env is active), or call the Python function.
4. Use the returned/written output paths.

## Output requirements

See the docstring section **Returns** below.

## Tooling rules

- **`autosaxs` is always tied to a Python environment** — see **Critical: `autosaxs` is a Python package** above before running anything.
- When in doubt (CI, fresh terminals, mixed conda/system shells), **always use the full path:** **`<path-to-env>/bin/autosaxs fit-sizes …`**.
- If you know the correct env is active on `PATH`, **`autosaxs fit-sizes …`** is fine.
- If the skill requires a config path (e.g. `config_path` / `config`) and no config file exists yet, run **`autosaxs get-default-config -o <dir>`** to materialize the bundled default config (`config_base.conf`) into a real file, then pass that path to the skill.
- Prefer the Python API (`autosaxs.skill.fit_sizes`) for scripting or tight integration inside Python.

## Autosaxs skill docstring

SAXS / small-angle x-ray scattering: run ATSAS GNOM (system=1/5) to obtain a size distribution function \(D(R)\) for a polydisperse system from a 1D SAXS curve (polydispersity; spheres/rods).

### Arguments

- `profile` (str): 1D path expression (file/dir/glob). Directories expand to `*.dat` (non-recursive).
- `output_dir` (str, default `.`): Output directory (one subdirectory per input profile).
- `shape` (str, default `spheres`): Polydisperse system model. Options:
    - `spheres`: GNOM `--system=1` (volume distribution for solid spheres).
    - `rods`: GNOM `--system=5` (length distribution for long cylinders). Requires `rad56_nm` (cylinder radius).
    - `ellipsoids`: accepted for API compatibility but **not supported by GNOM command-line** (GNOM system 2 is
      interactive-only). The skill will raise a clear error if selected.
- `rg_nm` (float | None): Optional metadata only (not passed to GNOM); recorded in outputs if set.
- `rmin_nm` (float | None): GNOM `--rmin` (nm). If omitted, not passed to GNOM.
- `rmax_nm` (float | None): GNOM `--rmax` (nm). If omitted, optimized in `[ε, 3 × rg_max]` from in-process `fit_guinier` (30 s max), scoring each trial as Total Estimate − neg_frac.
- `rad56_nm` (float | None): GNOM `--rad56` for `shape=rods` (nm cylinder radius). Ignored for spheres.
- `first` (int | None): GNOM `--first` (1-based). If omitted, taken from the low-q end of the Guinier interval from `fit_guinier`.
- `last` (int | None): GNOM `--last`. If omitted, not passed to GNOM.
- `alpha` (float | None): GNOM `--alpha`. If omitted, not passed to GNOM.
- `nr` (int | None): GNOM `--nr` (number of real-space points). If omitted, GNOM chooses automatically.
- `use_cache` (bool, default `False`): Enable/disable caching for this skill run.
- `stability_probe` (bool, default `True`): When True, run a close-fit rmax ensemble (5 GNOM calls) plus one force-zero-off boundary probe (1 GNOM call) for stability hints and D(R) plot overlays.

### Returns

`dict[str, str | list[str]]` with:

- `output_subdir`: The per-sample output directory used for this profile.
- `gnom_out_paths`: List of GNOM `.out` paths written for this profile (typically a single “best” `.out`).
- `best_gnom_out_path`: Path to the selected “best” GNOM `.out`.
- `fit_sizes_path`: Compact handoff YAML (`{base}_fit_sizes.yml`) — best fit, quality, analysis, and `model_mixture` hints.
- `fit_sizes_log_path` / `best_summary_path`: Extended run log YAML (`{base}_fit_sizes_log.yml`) — candidates, ensemble, failures.
- `fit_params_path` / `fit_sizes_hints_path` / `quality_passport_path`: Aliases of `fit_sizes_path` (backward compatibility).
- `best_symlink_out_path`: Best-effort symlink path to the selected `.out` (may be missing on some filesystems).
- `fit_vs_exp_png_path` / `fit_vs_exp_png_error`: Fit-vs-experiment plot output or error message.
- `best_dr_png_path` / `best_dr_png_error`: \(D(R)\) plot output or error message.
- `d_avg_nm` / `d_std_nm` / `pdi`: Mean size, standard deviation, and polydispersity index σ/⟨R⟩ from D(R).
- `dr_peak_positions_nm` / `dr_n_peaks`: Peak positions and count in D(R).
- `modality_class`: `monodisperse` \| `unimodal_polydisperse` \| `multimodal` \| `unknown`.
- `modality_confidence`: `high` \| `low` when parametric and peak-based modality hints disagree.
- `parametric_family` / `parametric_aic` / `n_components_suggested` / `mixture_dist_hint` / `parametric_peaks_nm`: Cheap post-hoc parametric hints on D(R).
- `stability_class`: `stable` \| `marginal` \| `unstable` from close-fit ensemble and force-zero-off probe.
- `ensemble_dir` / `ensemble_summary_path` / `close_fit_out_paths` / `force_zero_off_out_path`: Rmax stability probe artifacts (when `stability_probe=True`).
- `rmax_validation`: Pathology block from force-zero-off D(R) tail analysis.
- `rg_guinier_nm`: Guinier Rg (nm) when `fit_guinier` ran in-process.
- `total_estimate`: GNOM Total Estimate of the selected fit.
- `sizes_quality_class`: `high_quality` \| `acceptable` \| `failed`.
- `overall_status`: `HIGH QUALITY` \| `ACCEPTABLE` \| `FAILED`.
- `quality_rationale` / `user_tips`: Lists explaining the quality assessment.

### Python usage

```python
from autosaxs.skill import fit_sizes

out = fit_sizes(
    profile="subtracted/sub_sample_01.dat",
    output_dir="sizes",
    shape="spheres",
    use_cache=False,
)

print(out["best_gnom_out_path"])
```

### CLI usage

```bash
autosaxs fit-sizes subtracted/sub_sample_01.dat --output-dir sizes --shape spheres
```
