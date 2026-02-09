---
name: autocalib
description: Runs automatic calibration for 2D SAXS measurements. Detector geometry calibration is required to establish the correct q-axis and account for geometric distortions. It is an absolute prerequisite for accurate data and must be repeated whenever the geometry changes (e.g., when changing the detector-sample distance). Calibration is performed by measuring a sample with a known scattering pattern, such as silver behenate (AgBh).
license: MIT
compatibility: opencode
metadata:
  category: calibration
  tool: autocalib.py
---

## What I do

- Run **SAXS autocalibration** by executing the **autocalib.py** script in `skills/autocalib/`.
- The script accepts **command-line arguments**: calibrant image path, config file path, and optionally mask file path.
- **Working directory** is always derived from the calibrant image path (the directory containing the calibrant file); the user does not provide it. All calibration outputs are written there.
- Full calibration is always performed. The script writes calibration outputs (e.g. `calibration.png`, `integrator_params/`, `calibration_mask.png`) into the working directory and updates the config file with refined geometry.

## When to use me

- You have a **calibrant diffraction image** (`.tif`) and need to **calibrate** the detector geometry (center, distance, etc.) for SAXS.
- You want to run autocalibration in a single step with explicit paths (calibrant, config, mask) passed as CLI arguments.

## Command-line arguments (required inputs)

The **autocalib.py** script accepts the following as command-line arguments:

1. **Calibrant image path** — path to a TIFF image (e.g. `*.tif`) of the calibrant.
2. **Config file path** — path to a YAML config file (e.g. `*.conf`). Must contain the keys listed in "Config file (YAML) requirements" below.
3. **Mask file path** — path to a mask file (e.g. matching `mask*`). May be optional depending on `mask_config.mode` in the config (e.g. `from_file` or `combined` require a mask path).

## Requirements

To use this skill, your environment must have:
1. The `autosaxs` Python package installed via pip.
2. The `ATSAS-3.2.1` software package installed for SAXS data processing.  
   If `ATSAS-3.2.1` is not detected in your environment, the `autosaxs` package will display a warning.

## What to clarify with the user (if not specified)

1. **Calibrant image path** — full or relative path to the `.tif` calibrant file.
2. **Config file path** — full or relative path to the `.conf` (or other YAML) config file.
3. **Mask file path** — path to the mask file; whether a mask is required depends on the config (`mask_config.mode`).

## Config file (YAML) requirements

The config file is loaded as YAML and must provide (at least) the following structure:

- **calibrant_name** — name of the calibrant (e.g. for ring spacing lookup).
- **center_refinement** — `q_start`, `q_stop`, `min_segment_len` (center search).
- **detector_geometry** — `dist`, `wavelength`, `pixel_size`, `rot1`, `rot2`, `rot3` (initial geometry).
- **ring_search** — `q_stop`, `ring_I_threshold`, `r_max_px`, `r_step_px`.
- **r_beam_px** — beam radius in pixels.
- **mask_config** — mask configuration (e.g. `mode`: `from_file`, `combined`; automask parameters).

If any of these keys are missing, the script will fail when reading the config.

## Step-by-step algorithm

### 1) Resolve paths and directory

- **Directory**: Always derived from the calibrant image path (e.g. the parent directory of the calibrant file). The user does not provide it.
- **Calibrant path**: Must point to an existing `*.tif` file.
- **Config path**: Must point to an existing `*.conf` (or YAML) file.
- **Mask path**: Resolve the mask file path; if the config requires a mask (`mask_config.mode` in `['from_file', 'combined']`), the mask path must be provided and exist.

### 2) Invoke the script

- Run **autocalib.py** from `skills/autocalib/` (or ensure it is on the invocation path), passing the required values as command-line arguments:
  - Calibrant image path
  - Config file path
  - Mask file path (optional)
- Use the Python interpreter specified by the project (see workspace rules) when running the script.

Script usage example:
`python autocalib.py CALIBRANT_IMAGE.tif CONFIG.conf --mask-path=MASK.msk`

### 3) Outputs

- Calibration results are written in the **working directory** (the directory containing the calibrant image, derived from the calibrant path):
  - `calibration.png` — calibration visualization.
  - `integrator_params/` — integrator state (e.g. for downstream integration).
  - `calibration_mask.png` — mask visualization.
- The config file is updated in place with a **refined** section containing the refined geometry parameters.

## Verify the result

- Check that the **working directory** contains:
  - `calibration.png` (non-empty).
  - `integrator_params/` directory with expected integrator files.
  - `calibration_mask.png` if a mask was used.
- Check that the **config file** was updated with a `refined` section (refined geometry parameters).
- If the user reports failures, verify that the config file contains all required keys and that paths (calibrant, config, mask) are correct and readable.

## Common issues and solutions

- **autosaxs Python package is not installed**: Notify the user and recommend installing the package via:
  `pip install git+http://hpc.nano.sfedu.ru:8080/mikhail/saxsprocessing.git`
- **ATSAS package is not installed**: Notify the user and recommend installing ATSAS using the official link provided in the warning.
- **Config KeyError or missing keys**: The config YAML is missing a required key (e.g. `calibrant_name`, `detector_geometry`, `center_refinement`, `ring_search`, `r_beam_px`, `mask_config`). Ask the user to supply a complete config or add the missing keys.
- **Calibrant path empty or missing**: The script will not run calibration without a valid calibrant TIFF path. The calibrant path must be a valid file path. Ensure the user provides a valid calibrant path and that it is passed correctly as a CLI argument.
- **Mask required but not given**: When `mask_config.mode` in the config is `from_file` or `combined`, a valid mask file path must be provided; otherwise the script may fail. Clarify with the user or resolve the mask path (e.g. from the same directory as the calibrant).
