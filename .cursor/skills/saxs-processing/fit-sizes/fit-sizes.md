# `autosaxs fit-sizes` (subskill)

Ordinary procedure doc for the orchestrator — **not** a Cursor Agent Skill (`SKILL.md`). Open this file when the plan reaches this step.

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

This procedure wraps the `autosaxs fit-sizes` CLI command / `autosaxs.skill.fit_sizes` Python entry point.

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

SAXS / small-angle x-ray scattering: run ATSAS GNOM (system=1, spheres) to obtain a size distribution function \(D(R)\) for a polydisperse system from a 1D SAXS curve.

### Arguments

- `profile` (str): 1D path expression (file/directory/glob). Directories expand to `*.dat` (non-recursive).
- `output_dir` (str, default `.`): Directory where the outputs are written (one subdirectory per input profile).
- `shape` (str, default `spheres`): Polydisperse system model. Options: `spheres` (GNOM `--system=1` volume distribution for solid spheres), `rods` (GNOM `--system=5` length distribution for long cylinders, requires `rad56_nm` cylinder radius, deprecated), `ellipsoids` (accepted for API compatibility but **not supported by GNOM command-line** (GNOM system 2 is interactive-only), the skill will raise a clear error if selected).
- `rg_nm` (float | None): Optional metadata only (not passed to GNOM); recorded in outputs if set.
- `rmin_nm` (float | None): GNOM `--rmin` (nm). If omitted, not passed to GNOM.
- `rmax_nm` (float | None): GNOM `--rmax` (nm). If omitted, optimized in `[ε, 3 × rg_max]` from in-process `fit_guinier` (30 s max).
- `rad56_nm` (float | None): GNOM `--rad56` for `shape=rods` (nm cylinder radius), deprecated. Ignored for spheres.
- `first` (int | None): GNOM `--first` (1-based). If omitted, taken from the low-q end of the Guinier interval from `fit_guinier`.
- `last` (int | None): GNOM `--last`. If omitted, not passed to GNOM.
- `alpha` (float | None): GNOM `--alpha`. If omitted, not passed to GNOM.
- `nr` (int | None): GNOM `--nr` (number of real-space points). If omitted, GNOM chooses automatically.
- `use_cache` (bool, default `False`): Enable/disable caching for this skill run.

### Short parameter list

- shape: shape of the polydisperse system particles, currently only spheres supported
- rad56_nm: depricated, has no effect
- alpha: regularization parameter, auto-optimized if not set
- nr: number of fitted points, stick to the default

### Returns

`dict[str, str | list[str]]` with:

- `output_subdir`: Output subdirectory used for this sample.
- `atsas_fit_ok`: `True` on success; on failure also includes `gnom_failed`, `failure_reason`, `failure_message`, `failure_txt_path`.
- `gnom_out_paths`: List of `.out` files written by GNOM.
- `best_gnom_out_path`: Path to the primary/best `.out` file.
- `fit_sizes_path`: Compact YAML handoff for final fit, quality, and parametric hints.
- `fit_sizes_log_path`: Extended YAML log with ensemble/candidate/diagnostics.
- `best_summary_path`: Alias of `fit_sizes_log_path` (backward compatibility).
- `fit_params_path` / `fit_sizes_hints_path` / `quality_passport_path`: Aliases of `fit_sizes_path` (backward compatibility).
- `best_symlink_out_path`: Symlink (best-effort) pointing to the best `.out`.
- `fit_vs_exp_png_path`: Path to fit-vs-experiment plot PNG.
- `fit_vs_exp_png_error`: Error message (str) if plot failed, else empty string.
- `best_dr_png_path`: Path to D(R) plot PNG.
- `best_dr_png_error`: Error message (str) if plot failed, else empty string.
- `ensemble_dir`: Directory with close-fit ensemble artifacts.
- `ensemble_summary_path`: CSV/summary for the Rmax ensemble.
- `close_fit_out_paths`: List of `.out` files for close Rmax fits.
- `force_zero_off_out_path`: `.out` file for "force-zero-off" probe.
- `force_zero_off_pathology`: `"true"` / `"false"` / `""` — D(R) tail pathology from force-zero-off.
- `d_avg_nm`: Mean size (nm) from D(R).
- `d_std_nm`: Standard deviation of size (nm) from D(R).
- `pdi`: Polydispersity index σ/⟨R⟩.
- `dr_peak_positions_nm`: List of D(R) peak positions (nm) as strings.
- `dr_n_peaks`: Number of resolved D(R) peaks.
- `modality_class`: One of `monodisperse`, `unimodal_polydisperse`, `multimodal`, or `unknown`.
- `modality_confidence`: `high` or `low`, for peak/parametric agreement.
- `dmax_nm`: Maximum real-space size from the selected GNOM fit (nm).
- `rg_guinier_nm`: Guinier Rg (nm) from in-process analysis, if performed.
- `q_min_fit_nm`: Low-q bound (nm⁻¹) used in the GNOM fit.
- `total_estimate`: GNOM Total Estimate of the selected fit.
- `shannon_s_min`: Minimum Shannon sampling value.
- `shannon_class`: Shannon classification.
- `shannon_ok`: `"true"` / `"false"` / `""` — acceptable Shannon sampling.
- `shannon_tip`: Shannon interpretation guide.
- `parametric_family`: Best-fit parametric family name (e.g. `normal`, `gamma`).
- `parametric_R0_nm`: Parametric model center (nm).
- `parametric_width_nm`: Parametric model width (nm).
- `n_components_suggested`: Number of mixture components suggested by parametric fit.
- `mixture_dist_hint`: Parametric mixture distribution hint for D(R).
- `stability_class`: `stable`, `marginal`, `unstable`, or `unknown` Rmax/D(R) stability.
- `sizes_quality_class`: `high_quality`, `acceptable`, or `failed` from quality rules.
- `overall_status`: `HIGH QUALITY`, `ACCEPTABLE`, or `FAILED`.
- `quality_rationale`: List of strings with quality assessment justifications.
- `user_tips`: List of user-facing tips or warnings.

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
autosaxs fit-sizes subtracted/sub_sample_01.dat --output-dir sizes/ 
autosaxs fit-sizes subtracted/sub_sample_01.dat --shape spheres --rmin 2.0 --rmax 10.0 -o sizes/
```
