# autosaxs Skills Explained

This document explains the public *skills* exposed by the `autosaxs` package.

Skills are Python functions in `repos/autosaxs/skill.py` with a fixed signature designed to be callable both from Python and from the `autosaxs` CLI.

## CLI vs Python (how commands are wired)

The `autosaxs` command dispatches subcommands to the corresponding skill functions by introspecting their signatures. In practice:

- Run a skill from the CLI as `autosaxs <command> ...`.
- Every skill supports `--output-dir <path>` (maps to the skill's `output_dir` argument, default: `.`).
- Every skill supports caching by default; use `--no-cache` to disable it (maps to `use_cache=False` in Python).
- Positional arguments in the CLI match the skill signature order.
- Keyword options use `--kebab-case` names (underscores become `-`).

Caching details (enabled by default):

- When `use_cache=True`, the skill may write/read a hidden `.cache` YAML file inside its output directory.
- Re-running with the same inputs and relevant options can reuse previously generated output paths if the files still exist and are recent enough (output-integrity check).

---

## `calibrate`

Calibrate detector geometry using a calibration image and a config (ring-analysis + geometry refinement). This is a prerequisite for `integrate`.

### Arguments

- `calib_image` (str): Path to the calibration image (e.g. TIFF) used for ring analysis.
- `config_path` (str): Path to the autosaxs calibration config file. The config must include data required by the ring analysis and detector geometry refinement.
- `output_dir` (str, default `.`): Directory where results are written.
- `mask` (str | None, default `None`): Optional path to a mask used during ring analysis. Supports .txt (NuPy format), .msk (Fit2d)
- `mask_mode` (str, default `"f"`): Mask mode selector. One of `f/from_file`, `a/auto`, `c/combined`.
- `calibrant` (str, default `"AgBh"`): Calibrant name (must be in `pyFAI.calibrant.ALL_CALIBRANTS`).
- `use_cache` (bool, default `True`): Enable/disable caching for this skill run.

### Returns

`dict[str, str]` with these output path roles:

- `integrator_dir`: Directory containing the calibrated integrator (used by `integrate`).
- `refined_path`: Path to the refined calibration YAML.
- `calibration_plots_dir`: Directory containing calibration plots.
- `calibration_curve_plot_path`: Path to the calibration q/I curve plot (PNG).
- `calibration_mask_path`: Path to the calibration mask visualization (PNG).

### Python usage

```python
from autosaxs.skill import calibrate

out = calibrate(
    calib_image="AgBh.tif",
    config_path="config_autocalib.yml",
    output_dir="calibration",
    mask="mask.msk",
    mask_mode="f",
    calibrant="AgBh",
    use_cache=True,
)

print(out["integrator_dir"])
print(out["refined_path"])
```

### CLI usage

```bash
autosaxs calibrate AgBh.tif config_autocalib.yml --output-dir calibration --mask mask.msk
```

---

## `integrate`

Integrate 2D SAXS images to 1D curves (q, I, sigma) using a calibrated integrator produced by `calibrate`.

### Arguments

- `images` (list[str]): One or more paths to 2D SAXS images (e.g. TIFFs).
- `integrator_dir` (str): Path to the calibrated integrator directory (from `calibrate`).
- `output_dir` (str, default `.`): Directory where integrated curves are written.
- `npt` (int, default `1000`): Number of points in the output q grid.
- `use_cache` (bool, default `True`): Enable/disable caching for this skill run.

### Returns

`dict[str, str | list[str]]` with:

- `integrated_1d`: List of paths to integrated 1D `.dat` curves (one per input image).

### Python usage

```python
from autosaxs.skill import integrate

out = integrate(
    images=["/data/sample_01.tif", "/data/sample_02.tif"],
    integrator_dir="calibration/integrator",
    output_dir="integration",
    npt=1000,
    use_cache=True,
)

print(out["integrated_1d"])
```

### CLI usage

```bash
autosaxs integrate /data/sample_01.tif /data/sample_02.tif calibration/integrator --output-dir integration --npt 1000
```

---

## `integrate_proxy`

Integrate 2D TIFF image(s) to a 1D curve **without detector calibration**, using radial averaging in pixel-radius space.

This is intended for quick-look / debugging when you don’t have a calibrated integrator yet. The output `.dat` stores metadata indicating the x-axis is `r_px` (pixels), not physical q.

### Arguments

- `image` (str): Path to a `.tif` file **or** a directory containing `.tif` files.
- `output_dir` (str, default `.`): Directory where integrated curves are written.
- `mask` (str | None, default `None`): Optional mask path; same shape as the image. (`pyFAI` convention: masked pixels are excluded.)
- `cy` (float | None, default `None`): Optional beam center y in pixels. Must be set together with `cx`.
- `cx` (float | None, default `None`): Optional beam center x in pixels. Must be set together with `cy`.
- `npt` (int, default `1000`): Number of points in the output x grid.
- `use_cache` (bool, default `True`): Enable/disable caching for this skill run.

### Returns

`dict[str, str | list[str]]` with:

- `integrated_1d`: Path (or list of paths, if `image` is a directory) to integrated 1D `.dat` curves.

### Python usage

```python
from autosaxs.skill import integrate_proxy

out = integrate_proxy(
    image="raw/sample_01.tif",
    output_dir="integration_proxy",
    mask="mask.msk",
    npt=1000,
    use_cache=True,
)

print(out["integrated_1d"])
```

### CLI usage

```bash
autosaxs integrate_proxy raw/sample_01.tif --output-dir integration_proxy --mask mask.msk --npt 1000
```

---

## `subtract`

Subtract a buffer curve from a sample 1D profile. The current public interface supports match-tail scaling (`method="match_tail"`), optionally restricted to a q window.

### Arguments

- `sample_1d` (str): Path to the sample 1D `.dat` curve.
- `buffer_1d` (str): Path to the buffer 1D `.dat` curve (paired by convention with the sample).
- `output_dir` (str, default `.`): Directory where subtraction outputs are written.
- `method` (str, default `"match_tail"`): Buffer subtraction/scaling method.
- `q_min` (float | None, default `None`): Lower bound of q-range for scaling (only used if q_min/q_max logic is enabled).
- `q_max` (float | None, default `None`): Upper bound of q-range for scaling.
- `use_cache` (bool, default `True`): Enable/disable caching for this skill run.

Important constraint:

- If you set `q_max`, you must also set `q_min` (otherwise the skill raises `ValueError`).

### Returns

`dict[str, str]` with:

- `subtracted_1d`: Path to the subtracted curve `.dat`.
- `diff_plot_path`: Path to a diff plot PNG.
- `sub_plot_path`: Path to a subtracted curve plot PNG.

### Python usage

```python
from autosaxs.skill import subtract

out = subtract(
    sample_1d="integration/int_sample_01.dat",
    buffer_1d="integration/int_buffer.dat",
    output_dir="subtracted",
    method="match_tail",
    q_min=4.0,
    q_max=6.0,
    use_cache=True,
)

print(out["subtracted_1d"])
```

### CLI usage

```bash
autosaxs subtract integration/int_sample_01.dat integration/int_buffer.dat \
  --output-dir subtracted --method match_tail --q-min 4.0 --q-max 6.0
```

---

## `plot`

Generate standard plots for a 1D curve:

- Guinier plot (log(I) vs q^2)
- Kratky plot (I*q^2 vs q)
- log-log plot (log(I) vs log(q))

Also writes a Guinier `.dat` file (ln(I) vs q²) used downstream.

### Arguments

- `profile` (str): Path to the 1D `.dat` curve.
- `output_dir` (str, default `.`): Directory where plot files are written.
- `guinier_q_min` (float | None, default `None`): Lower q bound for selecting Guinier range (enables `guinier_dat_path`).
- `guinier_q_max` (float | None, default `None`): Upper q bound for selecting Guinier range.
- `use_cache` (bool, default `True`): Enable/disable caching for this skill run.

Important constraint:

- If you set `guinier_q_max`, you must also set `guinier_q_min` (otherwise the skill raises `ValueError`).

### Returns

`dict[str, str]` with:

- `guinier_plot_path`: Path to the Guinier PNG.
- `kratky_plot_path`: Path to the Kratky PNG.
- `loglog_plot_path`: Path to the log-log PNG.
- `guinier_dat_path`: Path to the Guinier `.dat` (q², ln(I)) written by the skill.

### Python usage

```python
from autosaxs.skill import plot

out = plot(
    profile="subtracted/sub_sample_01.dat",
    output_dir="plots",
    guinier_q_min=0.01,
    guinier_q_max=0.05,
    use_cache=True,
)

print(out["guinier_dat_path"])
```

### CLI usage

```bash
autosaxs plot subtracted/sub_sample_01.dat --output-dir plots --guinier-q-min 0.01 --guinier-q-max 0.05
```

---

## `plot_2d`

Render one 2D SAXS TIFF image (or all `.tif` images in a directory) to PNG using log-intensity scaling.

### Arguments

- `image` (str): Path to a `.tif` file **or** a directory containing `.tif` files.
- `output_dir` (str, default `.`): Directory where PNG(s) are written.
- `use_cache` (bool, default `True`): Enable/disable caching for this skill run.

### Returns

`dict[str, str | list[str]]` with:

- `plot_2d_png`: Path (or list of paths, if `image` is a directory) to generated PNG(s).

### Python usage

```python
from autosaxs.skill import plot_2d

out = plot_2d(
    image="raw/sample_01.tif",
    output_dir="plots_2d",
    use_cache=True,
)

print(out["plot_2d_png"])
```

### CLI usage

```bash
autosaxs plot_2d raw/sample_01.tif --output-dir plots_2d
```

---

## `guinier_analysis`

Run Guinier analysis on a 1D profile (including multiple strategies such as first-interval fits and an adaptive choice). The skill writes:

- a text results file
- an ATSAS-format `.dat` file for downstream tools
- a YAML file describing the chosen Guinier region parameters

### Arguments

- `profile` (str): Path to the 1D `.dat` curve.
- `output_dir` (str, default `.`): Directory where analysis outputs are written.
- `use_cache` (bool, default `True`): Enable/disable caching for this skill run.

### Returns

`dict[str, str]` with:

- `results_path`: Path to the results text file.
- `atsas_dat_path`: Path to the ATSAS-format `.dat` file.
- `guinier_region_path`: Path to the chosen Guinier region YAML.

### Python usage

```python
from autosaxs.skill import guinier_analysis

out = guinier_analysis(
    profile="subtracted/sub_sample_01.dat",
    output_dir="guinier",
    use_cache=True,
)

print(out["guinier_region_path"])
```

### CLI usage

```bash
autosaxs guinier_analysis subtracted/sub_sample_01.dat --output-dir guinier
```

---

## `fit_mixture`

Run MIXTURE fits on a 1D subtracted curve, select the best model by BIC, and write a comparison plot, size distribution plot, and results CSV.

### Arguments

- `profile` (str): Path to the 1D subtracted `.dat` curve.
- `output_dir` (str, default `.`): Directory where the MIXTURE outputs are written.
- `config` (dict): Loaded autosaxs config dict (must include a `mixture` section). This is required in Python usage.
- `q_min_nm` (float | None, default `None`): Optional q minimum bound (nm^-1) for the fitting range.
- `q_max_nm` (float | None, default `None`): Optional q maximum bound (nm^-1) for the fitting range.
- `use_cache` (bool, default `True`): Enable/disable caching for this skill run.

Important constraint:

- If you set `q_max_nm`, you must also set `q_min_nm` (otherwise the skill raises `ValueError`).

### Returns

`dict[str, str]` with:

- `output_subdir`: The subdirectory that contains MIXTURE outputs.
- `comparison_path`: Path to the MIXTURE comparison plot.
- `distributions_path`: Path to the MIXTURE size distributions plot.
- `results_csv_path`: Path to the MIXTURE results CSV.

### Python usage

```python
from autosaxs.skill import fit_mixture
from autosaxs.utils import load_config

out = fit_mixture(
    profile="subtracted/sub_sample_01.dat",
    output_dir="mixture",
    config=load_config("config_autosaxs.yml"),
    q_min_nm=0.8,
    q_max_nm=2.5,
    use_cache=True,
)

print(out["results_csv_path"])
```

### CLI usage

```bash
autosaxs fit_mixture subtracted/sub_sample_01.dat --output-dir mixture --q-min-nm 0.8 --q-max-nm 2.5
```

---

## `fit_bodies`

Run ATSAS `bodies` fits for multiple candidate shapes on a 1D profile, exporting fit files (FIR, PNG, YAML, CSV) and a comparison figure.

### Arguments

- `profile` (str): Path to the 1D `.dat` curve.
- `output_dir` (str, default `.`): Directory where `bodies` outputs are written.
- `use_cache` (bool, default `True`): Enable/disable caching for this skill run.

### Returns

`dict[str, str]` with:

- `output_subdir`: Directory containing the exported `bodies` fit artifacts.

The directory typically contains multiple per-shape FIT files plus aggregated `bodies_fits.yml` and `bodies_fits.csv` if any shapes successfully fit.

### Python usage

```python
from autosaxs.skill import fit_bodies

out = fit_bodies(
    profile="subtracted/sub_sample_01.dat",
    output_dir="bodies",
    use_cache=True,
)

print(out["output_subdir"])
```

### CLI usage

```bash
autosaxs fit_bodies subtracted/sub_sample_01.dat --output-dir bodies
```

---

## `fit_dammif`

Run ATSAS `dammif` (ab initio shape reconstruction) on a 1D profile. If a GNOM output file is available, you can provide it; otherwise the profile is used.

### Arguments

- `profile` (str): Path to the 1D `.dat` curve.
- `output_dir` (str, default `.`): Directory where `dammif` outputs are written.
- `gnom_path` (str | None, default `None`): Optional path to a GNOM `.out` file. If provided, `dammif` uses it.
- `use_cache` (bool, default `True`): Enable/disable caching for this skill run.

### Returns

`dict[str, str]` with:

- `output_subdir`: Directory containing `dammif` fit artifacts (FIR/CIF and summary files).

### Python usage

```python
from autosaxs.skill import fit_dammif

out = fit_dammif(
    profile="subtracted/sub_sample_01.dat",
    output_dir="dammif",
    gnom_path="guinier/sample_01_gnom.out",
    use_cache=True,
)

print(out["output_subdir"])
```

### CLI usage

```bash
autosaxs fit_dammif subtracted/sub_sample_01.dat --output-dir dammif --gnom-path guinier/sample_01_gnom.out
```

---

## `report_individual`

Build a per-sample PDF report from an existing pipeline directory. The skill scans `directory` for paths matching the provided `basename` and then assembles the report sections.

### Arguments

- `directory` (str): Path to the existing pipeline output directory (the place where intermediate results live).
- `basename` (str): Sample identifier used to match intermediate artifacts within `directory`.
- `output_dir` (str, default `.`): Directory where the PDF report is written.
- `output_path` (str | None, default `None`): Optional explicit output PDF path. If not provided, defaults to `<output_dir>/<basename>_report.pdf`.
- `use_cache` (bool, default `True`): Present for CLI parity; report generation does not use caching.

### Returns

`dict[str, Any]` with:

- `report_pdf_path`: Path to the generated PDF.

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
autosaxs report_individual pipeline_out sample_01 --output-dir reports
```

---

## `report_summary`

Build a summary PDF report for all samples found inside an existing pipeline directory. The skill discovers samples and combines plots/tables where data exists.

### Arguments

- `directory` (str): Path to the existing pipeline output directory.
- `output_dir` (str, default `.`): Directory where the summary PDF is written.
- `output_path` (str | None, default `None`): Optional explicit output PDF path. If not provided, defaults to `<output_dir>/summary_report.pdf`.
- `use_cache` (bool, default `True`): Present for CLI parity; report generation does not use caching.

### Returns

`dict[str, Any]` with:

- `report_pdf_path`: Path to the generated summary PDF.

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
autosaxs report_summary pipeline_out --output-dir reports
```

