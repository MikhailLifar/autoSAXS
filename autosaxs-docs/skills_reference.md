# autoSAXS skills reference

This document is the detailed reference for public *skills* exposed by the `autosaxs` package. For a short project overview, install notes, and GUIs, see the package [`README.md`](../README.md).

Skills are Python functions in the `autosaxs.skill` package (`src/autosaxs/skill/`) with a fixed signature designed to be callable both from Python and from the `autosaxs` CLI.

### CLI vs Python (how commands are wired)

The `autosaxs` command dispatches subcommands to the corresponding skill functions by introspecting their signatures. In practice:

- Run a skill from the CLI as `autosaxs <command> ...`.
- Every skill supports `-o` / `--output-dir <path>` (maps to the skill's `output_dir` argument, default: `.`).
- Every skill supports caching; use `--cache` to enable it (maps to `use_cache=True` in Python). Use `--no-cache` to explicitly disable it.
- Positional arguments in the CLI match the skill signature order.
- Keyword options use `--kebab-case` names (underscores become `-`).
- Brief CLI `--help` text for skill-specific options comes from the skill docstring section **`### Short parameter list`** (one bullet per parameter: ``- param_name: help text``).

### Path expansion (important API behavior)

Most skills take a **path expression** rather than a strict “single file”:

- A file path is used as-is.
- A directory expands to matching files (non-recursive):
  - 2D inputs: `*.tif`
  - 1D inputs: `*.dat`
- A glob expression is allowed (including `**`); results are sorted, and **empty expansion is an error**.

Note: `autosaxs integrate` accepts either a single path expression **or** multiple image paths on the CLI (the CLI passes a list; the skill normalizes it).

### Caching (opt-in)

- When `use_cache=True`, a skill may write/read a hidden `.cache` YAML file inside its output directory.
- Re-running with the same inputs and relevant options can reuse previously generated output paths if the files still exist and are recent enough (output-integrity check).
- On cache hits, the returned dict includes `from_cache=True` in addition to the usual output path keys.

### Related GUIs

- **`guisaxs-skills`** — form-driven runner over the skills API (requires `autosaxs[gui]`).
- **`guisaxs-liveview`** — watch-folder live integration / subtraction with optional monodisperse or polydisperse analysis windows.

See the package README for launch and layout details.

---

## `calibrate`

SAXS / small-angle x-ray scattering: calibrate detector geometry using calibrant image. This is a prerequisite for `integrate` (azimuthal integration).

### Arguments

- `calibrant_image` (str): Path to the calibrant image (e.g. TIFF).
- `output_dir` (str, default `.`): Directory where results are written.
- `config_path` (str | None, default `None`): Depricated. Path to a YAML config file with a `calibrate` section. When omitted, bundled defaults are used.
- `mask` (str): Path to a detector pixel mask. Supports .txt (NuPy format), .msk (Fit2d)
- `mask_mode` (str | None, default `None`): Mask mode selector (`f`/`from_file`, `a`/`auto`, `c`/`combined`). Defaults to `f`/`from_file`.
- `calibrant` (str | None, default `None`): Calibrant name (must be in `pyFAI.calibrant.ALL_CALIBRANTS`). Defaults to `AgBh`.
- `wavelength` (float | None, default `None`): X-ray wavelength in **Ångström**. Defaults to 1.445 Å.
- `dist_guess` (float | None, default `None`): Optional initial sample–detector distance in **metres** passed to pyFAI before geometry refinement. When omitted, distance is estimated from the innermost calibrant ring. Usually works well if not set.
- `use_cache` (bool, default `False`): Enable/disable caching for this skill run.

Important constraints:

- `mask` is always required by the skill and the CLI.

### Short parameter list

- mask_mode: Default: load mask from file as is.
- calibrant: name of the calibrant, default: AgBh.
- wavelength: X-ray wavelength in Ångström, default: 1.445 Å.
- dist_guess: Optional: initial sample-detector distance in metres (algorithm works good if this is not set).

### Returns

`dict[str, str]` with these output path roles:

- `integrator_dir`: Directory containing the calibrated integrator (used by `integrate`).
- `refined_path`: Path to the refined detector geometry YAML.
- `calibration_plots_dir`: Directory containing calibration plots.
- `calibration_curve_plot_path`: Path to the calibrantion q/I curve plot (PNG).
- `calibration_curve_dat_path`: Path to the calibrantion q/I curve (`.dat`, same format as integrated 1D curves).
- `calibration_mask_path`: Path to the detector pixel mask visualization (PNG).

### Python usage

```python
from autosaxs.skill import calibrate

out = calibrate(
    calibrant_image="AgBh.tif",
    output_dir="calibration/",
    mask="mask.msk",
    mask_mode="f",
    use_cache=False,
)

print(out["integrator_dir"])
print(out["refined_path"])
```

### CLI usage

```bash
autosaxs calibrate AgBh.tif --output-dir calibration --mask mask.msk
autosaxs calibrate AgBh.tif --conf my_config.conf -o calibration/
```

---

## `integrate`

SAXS / small-angle x-ray scattering: integrate 2D SAXS images to 1D curves (q, I, sigma) using a calibrated integrator produced by `calibrate` (azimuthal integration; q-space).

### Arguments

- `images` (str): Image path expression. Can be:
  - a single `.tif` file path
  - a directory (expands to `*.tif`, non-recursive)
  - a glob expression
  - a comma-separated list of file paths (e.g. from multi-file drag & drop)
- `integrator_dir` (str): Path to the calibrated integrator directory (from `calibrate`).
- `output_dir` (str, default `.`): Directory where integrated curves are written.
- `npt` (int, default `1000`): Number of points in the output q grid.
- `use_cache` (bool, default `False`): Enable/disable caching for this skill run.
- `validation_png` (bool, default `False`): If `True`, write a PNG next to each integrated curve showing the source image (log-intensity) with integrator-masked pixels highlighted in semi-transparent red.

### Short parameter list

- npt: Number of integrated points, default: 1000
- validation_png: Show validation image

### Returns

`dict[str, str | list[str]]` with:

- `integrated_1d`: List of paths to integrated 1D `.dat` curves (one per input image).
- `validation_png` (only when `validation_png=True`): List of paths to validation PNG(s), one per input image.

### Python usage

```python
from autosaxs.skill import integrate

out = integrate(
    images="/data/sample_*.tif",
    integrator_dir="calibration/integrator",
    output_dir="integration",
    npt=1000,
    use_cache=False,
)

print(out["integrated_1d"])
```

### CLI usage

```bash
autosaxs integrate "/data/sample_01.tif, /data/sample_02.tif" calibration/integrator       --output-dir integration --npt 1000
```

---

## `average`

SAXS / small-angle x-ray scattering: radiation-damage-aware averaging of per-frame 1D SAXS curves.

### Arguments

- `profiles` (str): 1D path expression (file/directory/glob). Directories expand to `*.dat` (non-recursive). Files are sorted lexicographically.
- `output_dir` (str, default `./averaged`): Directory where the outputs are written.
- `cormap_p_min` (float, default `0.05`): CorMap p-value threshold for borderline warnings.
- `chi2_max` (float, default `1.25`): Reject frame (and stop) when reduced chi-squared vs reference exceeds this value.
- `chi2_min` (float, default `0.9`): Warn when reduced chi-squared is below this value.
- `use_cache` (bool, default `False`): Enable/disable caching for this skill run.

### Short parameter list

- cormap_p_min: internal parameter, recommended not to change, default: 0.05
- chi2_max: internal parameter, recommended not to change, default: 1.25
- chi2_min: internal parameter, recommended not to change, default: 0.9

### Returns

dict[str, str] with:

- `averaged_1d`: Path to the merged `int_<prefix>.dat` curve.
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

---

## `integrate_proxy`

SAXS / small-angle x-ray scattering: integrate 2D TIFF image(s) to a 1D curve **without detector calibration**, using radial averaging in pixel-radius space (quick-look / debugging; not q-calibrated).

### Arguments

- `image` (str): 2D image path expression (file/directory/glob). Directories expand to `*.tif` (non-recursive).
- `output_dir` (str, default `.`): Directory where the outputs are written.
- `mask` (str | None, default `None`): Optional mask path; same shape as the image. (pyFAI convention: masked pixels are excluded.)
- `cy` (float | None, default `None`): Optional beam center y in pixels. Must be set together with `cx`. Defaults to None.
- `cx` (float | None, default `None`): Optional beam center x in pixels. Must be set together with `cy`. Defaults to None.
- `npt` (int, default `1000`): Number of points in the output x grid.
- `use_cache` (bool, default `False`): Enable/disable caching for this skill run.

Notes:

- If `cy/cx` are not provided, the skill **estimates** the center by radial-symmetry optimization and also writes a center diagnostic plot `*_center.png` into `output_dir`.
- If center estimation fails for an input, that item is skipped and the skill may return an empty list for `integrated_1d`.

### Returns

dict[str, str | list[str]] with:

- `integrated_1d`: Path (or list of paths, if `image` is a directory) to integrated 1D `.dat` curves.

### Python usage

```python
from autosaxs.skill import integrate_proxy

out = integrate_proxy(
    image="raw/sample_01.tif",
    output_dir="integration_proxy",
    mask="mask.msk",
    npt=1000,
    use_cache=False,
)

print(out["integrated_1d"])
```

### CLI usage

```bash
autosaxs integrate-proxy raw/sample_01.tif --output-dir integration_proxy --mask mask.msk --npt 1000
```

---

## `subtract`

SAXS / small-angle x-ray scattering: subtract a buffer curve from a sample 1D profile (background subtraction). Scaling uses either `point_match` (default)
or legacy `match_tail`, optionally restricted to a q window (`q_min` / `q_max`).

### Arguments

- `sample_1d` (str): Sample path expression (file/dir/glob). Directories expand to `*.dat` (non-recursive).
- `buffer_1d` (str): Path to the buffer 1D `.dat` curve (must be an existing file).
- `output_dir` (str, default `.`): Directory where subtraction outputs are written.
- `config_path` (str | None, default `None`): Optional path to a YAML config file with a `subtract` section. When omitted, bundled defaults apply for method/forms; q-window keys come from CLI or user file only.
- `method` (str | None, default `None`): `point_match` or `match_tail`. Defaults from bundled config when omitted.
- `q_min` (float): Lower bound of q-range (nm⁻¹). Required; may be overridden by a user config file `subtract` section.
- `q_max` (float): Upper bound of q-range (nm⁻¹); for `point_match` the match uses this as q intersect (upper edge of the window). Required; may be overridden by a user config file `subtract` section.
- `sample_form` / `buffer_form` (str | None): For `point_match` only — each is `linear`, `Porod`, or `Porod-plus-linear`.
- `point_match_factor` (float | None, default `None`): For `point_match`, scale satisfies `point_match_factor * I_sample_fit(q_max) = scale * I_buffer_fit(q_max)`.
- `scaling_factor` (float | None, default `None`): If provided, overrides automatic scaling and uses this factor directly (must be finite and > 0).
- `use_cache` (bool, default `False`): Enable/disable caching for this skill run.

The q window (`q_min`, `q_max`) is always required at the Python API and CLI. A user config file may supply values that override the arguments passed to `subtract()`.

### Short parameter list

- method: internal parameter, changing the default is not recommended, default: point-match
- sample_form: default: Porod+linear
- buffer_form: default: linear
- point_match_factor: internal parameter, changing the default is not recommended, default: 0.995
- q_min: Required, start of matching region
- q_max: Required, end of matching region, matching point
- scaling_factor: Manual scaling factor. When this set, it replaces auto-scale

### Returns

`dict[str, str]` with:

- `subtracted_1d`: Path to the subtracted curve `.dat`.
- `sub_plot_path`: Path to the subtracted curve PNG (log I vs q).
- `diff_plot_path`: Path to a diff plot PNG.
- `diff_log_plot_path`: Path to a diff plot PNG with log(I) vs q.
Subtraction quality (`correct` or `over-subtracted`) is written into the subtracted `.dat` metadata
(``subtract.correctness``) and into per-sample report fragments (individual Markdown and summary YAML).
The individual report embeds the subtracted curve from the `.dat` (not from `sub_plot_path`).

### Python usage

```python
from autosaxs.skill import subtract

out = subtract(
    sample_1d="integration/int_sample_01.dat",
    buffer_1d="integration/int_buffer.dat",
    output_dir="subtracted",
    method="point_match",
    q_min=4.0,
    q_max=6.0,
    use_cache=False,
)

print(out["subtracted_1d"])
```

### CLI usage

```bash
autosaxs subtract integration/int_sample_01.dat integration/int_buffer.dat       --output-dir subtracted --method point_match --q-min 4.0 --q-max 6.0
```

---

## `plot`

SAXS / small-angle x-ray scattering: generate standard diagnostic plots for a 1D curve (Guinier, Kratky, log-log):

- Guinier plot (log(I) vs q^2)
- Kratky plot (I*q^2 vs q)
- log-log plot (log(I) vs log(q))

### Arguments

- `profile` (str): 1D path expression (file/directory/glob). Directories expand to `*.dat` (non-recursive).
- `output_dir` (str, default `.`): Directory where plot files are written.
- `guinier_q_min` (float | None, default `None`): Lower q bound for selecting Guinier range (enables `guinier_dat_path`).
- `guinier_q_max` (float | None, default `None`): Upper q bound for selecting Guinier range.
- `use_cache` (bool, default `False`): Enable/disable caching for this skill run.

Important constraint:

- If you set `guinier_q_max`, you must also set `guinier_q_min` (otherwise the skill raises `ValueError`).

### Returns

dict[str, str] with:

- `guinier_plot_path`: Path to the Guinier PNG.
- `kratky_plot_path`: Path to the Kratky PNG.
- `loglog_plot_path`: Path to the log-log PNG.
- `guinier_dat_path`: Path to the Guinier `.dat` (q², ln(I)) written by the skill (always written; independent of `guinier_q_min/max`).

### Python usage

```python
from autosaxs.skill import plot

out = plot(
    profile="subtracted/sub_sample_01.dat",
    output_dir="plots",
    guinier_q_min=0.01,
    guinier_q_max=0.05,
    use_cache=False,
)

print(out["guinier_dat_path"])
```

### CLI usage

```bash
autosaxs plot subtracted/sub_sample_01.dat --output-dir plots --guinier-q-min 0.01 --guinier-q-max 0.05
```

---

## `plot_2d`

SAXS / small-angle x-ray scattering: render 2D SAXS TIFF image(s) to PNG using log-intensity scaling (2D detector view).

### Arguments

- `image` (str): 2D path expression (file/directory/glob). Directories expand to `*.tif` (non-recursive).
- `output_dir` (str, default `.`): Directory where PNG(s) are written.
- `use_cache` (bool, default `False`): Enable/disable caching for this skill run.

### Returns

`dict[str, str | list[str]]` with:

- `plot_2d_png`: Path (or list of paths, if `image` is a directory) to generated PNG(s).

### Python usage

```python
from autosaxs.skill import plot_2d

out = plot_2d(
    image="raw/sample_01.tif",
    output_dir="plots_2d",
    use_cache=False,
)

print(out["plot_2d_png"])
```

### CLI usage

```bash
autosaxs plot-2d raw/sample_01.tif --output-dir plots_2d
```

---

## `fit_guinier`

SAXS / small-angle x-ray scattering: Do Guinier analysis on a 1D profile (Rg, I(0), Rg span, Guinier interval, quality). 

### Arguments

- `profile` (str): 1D path expression (file/directory/glob). Directories expand to `*.dat` (non-recursive).
- `output_dir` (str, default `.`): Directory where the outputs are written.
- `first` (int | None, default `None`): 1-based start point for a fixed-interval Guinier fit (requires `last`).
- `last` (int | None, default `None`): 1-based end point (inclusive) for a fixed-interval Guinier fit (requires `first`).
- `use_cache` (bool, default `False`): Enable/disable caching for this skill run.

### Returns

`dict[str, str]` with:

- `results_path`: Path to the results text file.
- `atsas_dat_path`: Path to the ATSAS-format `.dat` file.
- `guinier_plot_path`: Path to the Guinier fit PNG.

### Python usage

```python
from autosaxs.skill import fit_guinier

out = fit_guinier(
    profile="subtracted/sub_sample_01.dat",
    output_dir="guinier",
    use_cache=False,
)

print(out["results_path"])
```

### CLI usage

```bash
autosaxs fit-guinier subtracted/sub_sample_01.dat --output-dir guinier/
autosaxs fit-guinier subtracted/sub_sample_01.dat --first 10 --last 100 -o guinier/
```

---

## `analyze_kratky`

SAXS / small-angle x-ray scattering: dimensionless Kratky conformation analysis on a 1D profile.

### Arguments

- `profile` (str): 1D path expression (file/directory/glob). Directories expand to `*.dat` (non-recursive).
- `output_dir` (str, default `.`): Directory where the outputs are written.
- `config_path` (str | None, default `None`): Deprecated. YAML/config with a `analyze_kratky` section. When omitted, bundled defaults apply.
- `rg_nm` (float | None, default `None`): Radius of gyration in nm. If omitted, taken from in-process Guinier.
- `i0` (float | None, default `None`): Forward scattering I(0). If omitted, taken from in-process Guinier.
- `q_min`, `q_max` (float | None): Optional q-range (nm⁻¹) applied before analysis. Defaults to None.
- `globular_x_min`, `globular_x_max`, `globular_y_min`, `globular_y_max`: Globular peak bands. Defaults to 1.65, 1.85, 1.0, 1.2 respectively.
- `elongated_x_min`, `elongated_x_max`, `elongated_y_min`: Elongated peak bands. Defaults to 1.85, 2.5, 1.15 respectively.
- `coil_plateau_y`, `coil_plateau_tol`, `coil_high_x_min`: Coil / Debye-plateau detection. Defaults to 2.0, 0.25, 3.0 respectively.
- `x_search_min`, `x_search_max`: Peak search window in q·Rg. Defaults to 0.5, 4.0 respectively.
- `use_cache` (bool, default `False`): Enable/disable caching for this skill run.

### Returns

`dict` with:

- `results_path`: Path to the text results file.
- `kratky_plot_path`: Path to the classical Kratky PNG (I·q² vs q).
- `kratky_dimensionless_plot_path`: Path to the dimensionless Kratky PNG.
- `kratky_classical_dat_path`: Path to classical Kratky `.dat`.
- `kratky_dimensionless_dat_path`: Path to dimensionless Kratky `.dat`.
- `classification`: Assigned conformation label.
- `x_max`, `y_max`: Dimensionless peak coordinates (q·Rg, Y).

### Python usage

```python
from autosaxs.skill import analyze_kratky

out = analyze_kratky(
    profile="subtracted/sub_sample_01.dat",
    output_dir="kratky",
    use_cache=False,
)

print(out["classification"])
```

### CLI usage

```bash
autosaxs analyze-kratky subtracted/sub_sample_01.dat --output-dir kratky
autosaxs analyze-kratky subtracted/sub_sample_01.dat --rg-nm 3.2 --i0 1.05 --output-dir kratky
```

---

## `fit_distances`

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

---

## `fit_sizes`

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

---

## `model_dr_mc`

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

---

## `model_mixture`

SAXS / small-angle x-ray scattering: run MIXTURE fits on a 1D subtracted curve, select the best model by BIC, and write a comparison plot, size distribution plot, and results CSV.

### Arguments

- `profile` (str): 1D path expression (file/directory/glob). Directories expand to `*.dat` (non-recursive).
- `output_dir` (str, default `.`): Directory where the outputs are written.
- `config_path` (str | None, default `None`): Deprecated. YAML/config with a `model_mixture` section. When omitted, bundled defaults apply.
- `q_min_nm` / `q_max_nm` (float | None): Optional q bounds (nm^-1). Defaults to None.
- `maxit` (int, default `100`): Number of optimization iterations. Defaults to 100.
- `max_nph` (int, default `3`): Number of phases.
- `plot_I_q` (bool, default `False`): Write I vs q fit comparison plot (labels show BIC).
- `plot_logI_logq` (bool, default `False`): Write log I vs log q fit comparison plot (labels show BIC_log).
- `plot_logI_q` (bool, default `True`): Write log I vs q fit comparison plot (labels show chi2).
- `r_min` (float | None): Minimum radius (nm). Defaults to `0.1`. Converted to Å internally for ATSAS MIXTURE.
- `r_max` (float | None): Maximum radius (nm). Defaults to `rmax_nm` from in-process `fit_sizes`.
- `poly_min` (float | None): Minimum polydispersity (nm). Defaults to `0.05`.
- `poly_max` (float | None): Maximum polydispersity (nm). Defaults to `0.5 × r_max`.
- `use_cache` (bool, default `False`): Enable/disable caching for this skill run.

### Short parameter list

- q_min_nm: start of fit region
- q_max_nm: end of fit region
- maxit: number of optimization iterations, default: 100
- r_min: size lower bound
- r_max: size upper bound
- poly_min: spread lower bound
- poly_max: spread upper bound

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
autosaxs model-mixture subtracted/sub_sample_01.dat --output-dir mixture/ 
autosaxs model-mixture subtracted/sub_sample_01.dat--q-min-nm 0.8 --q-max-nm 2.5 --r-max 10.0 -o mixture/
```

---

## `model_bodies`

SAXS / small-angle x-ray scattering: run ATSAS `bodies` shape fitting for multiple candidate shapes on a 1D profile.

### Arguments

- `profile` (str): 1D path expression (file/directory/glob). Directories expand to `*.dat` (non-recursive).
- `output_dir` (str, default `.`): Directory where the outputs are written.
- `config_path` (str | None, default `None`): Deprecated. YAML/config with a `model_bodies` section. When omitted, bundled defaults apply.
- `shapes` (list[str] | None, default `None`): Subset of body model names to fit. `None` or empty means fit **all** models (single `bodies` invocation). A non-empty list runs `bodies --body=...` per shape.
- `first` (int | None, default `None`): Passed to `bodies` as `--first` (1-based data point index). If omitted, taken from the low-q end of the Guinier interval from in-process `fit_guinier`.
- `last` (int | None, default `None`): Passed to `bodies` as `--last` (1-based data point index). Omitted when `None`.
- `use_cache` (bool, default `False`): Enable/disable caching for this skill run.

### Returns

`dict[str, str]` with:

- `output_subdir`: Directory containing the exported `bodies` fit artifacts.

The directory typically contains multiple per-shape FIT files plus aggregated `bodies_fits.yml` and `bodies_fits.csv` if any shapes successfully fit. Each fitted shape also gets `{shape}_pr.dat` and `{shape}_pr.png` (GNOM-style p(r) from the voxel DAM used for 3D views, via Monte Carlo bead-pair sampling).

### Python usage

```python
from autosaxs.skill import model_bodies

out = model_bodies(
    profile="subtracted/sub_sample_01.dat",
    output_dir="bodies",
    shapes=["cylinder", "ellipsoid"],
    first=10,
    last=120,
    use_cache=False,
)

print(out["output_subdir"])
```

### CLI usage

```bash
autosaxs model-bodies subtracted/sub_sample_01.dat --output-dir bodies --shapes cylinder ellipsoid --first 10 --last 120
```

---

## `model_dam`

SAXS / small-angle x-ray scattering: ab initio bead-model shape reconstruction with ATSAS DAMMIF, optionally followed by DAMAVER ensemble averaging. When no GNOM `.out` is supplied, `fit_distances` is run in-process to obtain one.

### Arguments

- `profile` (str): 1D path expression (file/directory/glob). Directories expand to `*.dat` (non-recursive).
- `output_dir` (str, default `.`): Directory where the outputs are written.
- `gnom_path` (str | None, default `None`): Optional path to a GNOM/DATGNOM `.out` file for DAMMIF. If omitted, `fit_distances` is run in-process on `profile` and its `best_gnom_out_path` is used.
- `n_runs` (int, default `1`): Number of independent DAMMIF runs. When `>1`, DAMAVER is run on the particle models. Defaults to 1.
- `dammif_mode` (str, default `fast`): DAMMIF annealing mode: `fast` or `slow`. Defaults to `fast`.
- `visualize_all` (bool, default `False`): When True, write PNGs/GIFs under `{output}/visuals/` (synced per-run rotation GIFs, overlap, occupancy threshold; nm scale bar; no run/title captions). Defaults to `False`.
- `use_cache` (bool, default `False`): Enable/disable caching for this skill run.

### Returns

`dict[str, str | list[str]]` with:

- `output_subdir`: Directory containing DAMMIF fit artifacts (FIR/CIF and summary files). Each replica also gets `{rep}_pr.dat` and `{rep}_pr.png` (GNOM-style p(r) from DAM bead pairs via Monte Carlo).
- `best_cif_path`: Symlink `best.cif` pointing at the most probable particle CIF (the sole run when `n_runs=1`).
- `best_view_path`: Path to ``best_view.png`` (isosurface + fit overlay for the best model); empty if unavailable.
- `frequency_map_path`: Path to the DAMAVER frequency/occupancy map CIF (empty string when `n_runs=1`).
- `visuals_dir`, `overlap_png`, `overlap_gif`, `occupancy_png`, `occupancy_gif`, `occupancy_thresholds_png`, `run_gifs` when `visualize_all=True` (empty strings / empty list otherwise).

### Python usage

```python
from autosaxs.skill import model_dam

out = model_dam(
    profile="subtracted/sub_sample_01.dat",
    output_dir="dammif",
    gnom_path="guinier/sample_01_gnom.out",
    n_runs=1,
    dammif_mode="fast",
    visualize_all=False,
    use_cache=False,
)

print(out["output_subdir"], out["best_cif_path"])
```

### CLI usage

```bash
autosaxs model-dam subtracted/sub_sample_01.dat --output-dir dammif --n-runs 1 --dammif-mode fast
autosaxs model-dam subtracted/sub_sample_01.dat --output-dir dammif --n-runs 5 --visualize-all
```

---

## `model_density`

SAXS / small-angle x-ray scattering: ab initio continuous electron-density reconstruction with DENSS (Grant protocol; density map / FSC resolution / voxel σ map).

### Arguments

- `profile` (str): 1D path expression (file/directory/glob). Directories expand to `*.dat` (non-recursive).
- `output_dir` (str, default `.`): Directory where the outputs are written.
- `gnom_path` (str | None, default `None`): Optional GNOM/DATGNOM `.out` used only for \(D_{\max}\) (nm→Å). Smooth \(I(q)\) comes from the staged Å `.dat` (DENSS may fit internally).
- `mode` (str, default `pilot`): Protocol stage: `pilot`, `average`, or `refined`. Defaults to `pilot`.
- `denss_mode` (str, default `slow`): DENSS algorithm mode: `slow`, `fast`, or `membrane`. Defaults to `slow`.
- `n_maps` (int, default `20`): Number of reconstructions for `average`/`refined` (ignored in `pilot`; must be ≥2 when used). Defaults to 20.
- `n_jobs` (int, default `1`): Parallel cores for denss-all. Defaults to 1.
- `visualize_all` (bool, default `True`): When True, write slice GIF/PNG and rotating density/σ GIFs under `{output}/visuals/`. Defaults to `True`.
- `use_cache` (bool, default `False`): Enable/disable caching for this skill run.

### Short parameter list

- mode: Run mode: pilot - quick map view; average - average map across mutliple runs; refined - refined from averaged; default - pilot
- denss_mode: Internal parameter, recommended not to change, default: slow
- n_maps: Number of independent run for average; default 20
- visualize_all: Run visualizations, default: true

### Returns

`dict[str, str]` with:

- `output_subdir`: Directory containing DENSS artifacts for this sample.
- `density_map_path`: Primary density MRC (pilot map, average map, or refined map).
- `avg_map_path`: Averaged MRC path when averaging ran; empty string for `pilot`.
- `sigma_map_path`: Voxel-wise density σ MRC from denss-all `*_aligned.mrc` stack when averaging ran; empty string for `pilot`.
- `fsc_path`: FSC curve path when averaging ran; empty string otherwise.
- `map_fit_path`: Calculated vs experimental fit file when present; else empty.
- `denss_log_path`: Main log for the completed mode.
- `visuals_dir`, `slices_gif`, `midplanes_png`, `density_rotate_gif`, `sigma_rotate_gif` when `visualize_all=True` (empty strings otherwise; `sigma_rotate_gif` empty in `pilot`).

### Python usage

```python
from autosaxs.skill import model_density

out = model_density(
    profile="subtracted/sub_sample_01.dat",
    output_dir="denss",
    mode="pilot",
    denss_mode="slow",
    use_cache=False,
)

print(out["density_map_path"])
```

### CLI usage

```bash
autosaxs model-density subtracted/sub_sample_01.dat --output-dir denss/ 
autosaxs model-density subtracted/sub_sample_01.dat --mode average --denss-mode slow --n-maps 10 --n-jobs 4 -o denss/
```

---

## `process_monodisperse`

SAXS / small-angle x-ray scattering: run the monodisperse single-profile quality pipeline
(Guinier → dimensionless Kratky → DATGNOM p(r) / Shannon–ΔRg passport → optional DAMMIF
when quality gates pass → per-sample PDF report).

### Arguments

- `profile` (str): 1D path expression (file/directory/glob of `*.dat`). Directories expand non-recursively.
- `output_dir` (str, default `.`): Pipeline root; leaf skills write under subdirectories here.
- `config_path` (str | None, default `None`): Deprecated. Optional YAML config forwarded to leaf skills.
- `first` / `last` (int | None): Optional fixed Guinier interval (1-based); both required together.
  Guinier `first` is forwarded to DATGNOM; Guinier `last` is **not** passed to DATGNOM
  (window too narrow for p(r)).
- `smooth` (float | None, default `None`): Optional DATGNOM `--smooth` for `fit_distances`.
- `n_runs` (int, default `5`): DAMMIF replica count for `model_dam` when the quality gate passes.
- `use_cache` (bool, default `False`): Forwarded to leaf skills.

### Returns

`dict` with:

- `report_pdf_path`: Primary PDF quality passport (when written).
- `assembled_report_md_path`: Merged Markdown report.
- `pipeline_dir`: The `output_dir` used as the pipeline root.
- `basename`: Sample basename used for report assembly.
- `model_dam_ran`: Whether `model_dam` was invoked.
- `model_dam_skip_reason`: Why DAMMIF was skipped (empty when run).
- `fit_guinier`: Return dict from `fit_guinier`.
- `analyze_kratky`: Return dict from `analyze_kratky`.
- `fit_distances`: Return dict from `fit_distances`.
- `model_dam`: Return dict from `model_dam` (empty dict when skipped).
- `report_individual`: Return dict from `report_individual`.

### Python usage

```python
from autosaxs.skill import process_monodisperse

out = process_monodisperse(
    profile="subtracted/sub_sample_01.dat",
    output_dir="mono_out",
)
print(out["report_pdf_path"])
```

### CLI usage

```bash
autosaxs process-monodisperse subtracted/sub_sample_01.dat --output-dir mono_out
```

---

## `report_individual`

SAXS / small-angle x-ray scattering: build a per-sample report from an existing pipeline directory.

Assembles decentralized ``*_report_individual.md`` fragments, writes
``<pipeline>/reports/<basename>_assembled_report.md``, and builds the PDF with **ReportLab**
from that Markdown (headings, text, images, simple tables).

### Arguments

- `directory` (str): Path to the existing pipeline output directory (the place where intermediate results live).
- `basename` (str): Sample identifier used to match intermediate artifacts within `directory`.
- `output_dir` (str, default `.`): Unused for default paths; PDF/MD default to ``<directory>/reports/``.
- `output_path` (str | None, default `None`): Output PDF path; default ``<directory>/reports/<basename>_report.pdf``.
- `output_md_path` (str | None, default `None`): Optional path for merged Markdown.
- `write_pdf` (bool, default `True`): Whether to emit a PDF.
- `use_cache` (bool, default `False`): Present for CLI parity; unused.

### Returns

`dict[str, Any]` with:

- `report_pdf_path`: Path to the generated PDF when ``write_pdf`` is True.
- `assembled_report_md_path`: Path to merged Markdown.
- `fragments_found`: Number of fragment files merged.

### Python usage

```python
from autosaxs.skill import report_individual

out = report_individual(
    directory="pipeline_out",
    basename="sample_01",
    output_dir="reports",
)

print(out["report_pdf_path"])
```

### CLI usage

```bash
autosaxs report-individual pipeline_out sample_01 --output-dir reports
```

---

## `report_summary`

SAXS / small-angle x-ray scattering: build a summary report for all samples in a pipeline directory.

Merges decentralized ``*_report_summary.yaml`` files into Markdown under
``<directory>/reports/summary_assembled_report.md`` and renders the PDF with **ReportLab**
from that Markdown.

### Arguments

- `directory` (str): Path to the existing pipeline output directory.
- `output_dir` (str, default `.`): Unused for default paths; outputs go under ``<directory>/reports/``.
- `output_path` (str | None, default `None`): Output PDF path; default ``<directory>/reports/summary_report.pdf``.
- `output_md_path` (str | None, default `None`): Output path for merged summary Markdown.
- `write_pdf` (bool, default `True`): Whether to emit a PDF.
- `use_cache` (bool, default `False`): Present for CLI parity; unused.

### Returns

`dict[str, Any]` with:

- `report_pdf_path`: Path to the generated PDF when requested.
- `assembled_summary_md_path`: Merged Markdown path.

### Python usage

```python
from autosaxs.skill import report_summary

out = report_summary(
    directory="pipeline_out",
    output_dir="reports",
)

print(out["report_pdf_path"])
```

### CLI usage

```bash
autosaxs report-summary pipeline_out --output-dir reports
```

---
