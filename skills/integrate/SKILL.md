---
name: integrate
description: Runs 2D-to-1D SAXS integration on a TIFF image using a pre-existing integrator (saved in a directory). The image is azimuthally integrated to produce a 1D curve (q, I, σ) saved as a .dat file.
license: MIT
compatibility: opencode
metadata:
  category: processing
  tool: integrate.py
---

## What I do

- Run **SAXS 2D-to-1D integration** by executing the **integrate.py** script in `skills/integrate/`.
- **Calibration must be done first** to obtain the directory with calibration results (integrator parameters). The integrate script uses that directory; it does not perform calibration.
- The script accepts **command-line arguments**: path to the integrator directory and path to the 2D image (e.g. `*.tif`).
- **Output directory** is always the directory containing the image file; the user does not provide it. The integrated curve is written there.
- Integration is always performed; existing result files are not skipped.
- Output written to the output directory: integrated 1D curve **`int_<basename>.dat`**, where `<basename>` is the stem of the input TIFF filename.

## When to use me

- **Calibration has already been done** (or the user will provide calibration parameters, e.g. as an archive), so you have or can obtain the **integrator directory**.
- You have a **2D SAXS image** (`*.tif`) that you want to **integrate** to a 1D curve.
- You want to run integration in a single step with explicit paths (integrator directory and image path) passed as CLI arguments.

## Command-line arguments (required inputs)

The **integrate.py** script accepts the following as command-line arguments:

1. **Integrator path** — path to the directory where the integrator is stored (the script loads the integrator from this directory).
2. **Image path** — path to the 2D SAXS image to integrate (e.g. `*.tif`).

## Requirements

To use this skill, your environment must have:
1. The `autosaxs` Python package installed (e.g. via pip from the project repository).

## What to clarify with the user (if not specified or clear from context)

1. **Integrator path** — full or relative path to the directory containing the integrator (calibration) data.
2. **Image path** — full or relative path to the TIFF image (`*.tif`) to integrate.

**If it is not clear what the integrator directory is**, ask the user:
- Whether they want to **run calibration first** (e.g. using the autocalib skill) to produce that directory, or
- Whether they want to **provide calibration parameters as an archive** (you will need to unpack it using 7z program).

## Step-by-step algorithm

### 1) Resolve paths and directory

- **Output directory**: Always derived from the image path (the parent directory of the image file). The user does not provide it.
- **Integrator path**: Must point to an existing **directory** with valid integrator data.
- **Image path**: Must point to an existing **TIFF** file (e.g. `*.tif`).

### 2) Invoke the script

- Run **integrate.py** from `skills/integrate/` (or ensure it is on the invocation path), passing the required values as command-line arguments:
  - Integrator path (directory)
  - Image path (TIFF file)
- Use the Python interpreter specified by the project (see workspace rules) when running the script.

Script usage example:
`python integrate.py INTEGRATOR_DIR IMAGE.tif`

### 3) Outputs

- Integration result is written in the **output directory** (the directory containing the image file):
  - **`int_<basename>.dat`** — integrated 1D SAXS curve (q, I, σ and metadata).
- `<basename>` is the stem of the input TIFF filename (e.g. `frame_001.tif` → `int_frame_001.dat`).

## Verify the result

- Check that the **output directory** contains **`int_<basename>.dat`** (non-empty), where `<basename>` corresponds to the input image name.
- If the user reports failures, verify that the integrator path is a valid directory with the expected data and that the image path is a valid, readable TIFF file.

## Common issues and solutions

- **autosaxs Python package is not installed**: Notify the user and recommend installing the package (e.g. from the project repository).
- **Integrator directory missing or unclear**: Ask the user whether to run calibration first (e.g. autocalib skill) or to provide calibration parameters (e.g. as an archive).
- **Integrator directory invalid or incomplete**: The script may fail to load the integrator. Suggest re-running calibration (e.g. autocalib skill) or checking that the directory contains the expected files.
- **Image path missing or invalid**: The script will not run integration without a valid `*.tif` path. Ensure the user provides a valid path and that it is passed correctly as a CLI argument.
