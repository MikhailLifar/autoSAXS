---
name: get-simple-saxs-descriptors
description: Computes SAXS structural descriptors (Rg, I(0), Dmax, Porod volume, molecular weight estimates) from a 1D SAXS curve. The descriptors are saved as a text file alongside the GNOM P(r) output.
license: MIT
compatibility: opencode
metadata:
  category: processing
  tool: get_descriptors.py
---

## What I do

- Run **SAXS descriptor extraction** by executing the **get_descriptors.py** script in `skills/get-simple-saxs-descriptors/`.
- The script accepts **command-line arguments**: path to the 1D SAXS data file (e.g. `*.dat`).
- **Output directory** is always the directory containing the input `.dat` file; the user does not provide it. Results are written there.
- Analysis is always performed; existing result files are not skipped.
- Output written to the output directory: **`<basename>_results.txt`** (Rg, I(0), Quality, Dmax, Porod volume, MW estimates) and **`<basename>.out`** (GNOM P(r) file), where `<basename>` is the stem of the input `.dat` filename.

## When to use me

- You have a **1D SAXS curve** (`*.dat`) that you want to **analyze** to obtain structural descriptors.
- You want Rg, I(0), Dmax, Porod volume, and molecular weight estimates from an integration or subtraction result.
- You want to run descriptor extraction in a single step with an explicit path passed as a CLI argument.

## Command-line arguments (required inputs)

The **get_descriptors.py** script accepts the following as command-line arguments:

1. **Path to analysis** — path to the 1D SAXS data file to analyze (e.g. `*.dat`).

## Requirements

To use this skill, your environment must have:
1. The `autosaxs` Python package installed (e.g. via pip from the project repository).
2. ATSAS installed and on `PATH`.

## What to clarify with the user (if not specified or clear from context)

1. **Path to analysis** — full or relative path to the 1D SAXS data file (`*.dat`) to analyze.

**If it is not clear what file to analyze**, ask the user for the path to the `.dat` file.

## Step-by-step algorithm

### 1) Resolve paths and directory

- **Output directory**: Always derived from the path to analysis (the parent directory of the `.dat` file). The user does not provide it.
- **Path to analysis**: Must point to an existing **1D SAXS data file** (e.g. `*.dat`).

### 2) Invoke the script

- Run **get_descriptors.py** from `skills/get-simple-saxs-descriptors/` (or ensure it is on the invocation path), passing the required value as a command-line argument:
  - Path to the 1D SAXS data file
- Use the Python interpreter specified by the project (see workspace rules) when running the script.

Script usage example:
`python get_descriptors.py PATH_TO_DAT_FILE`

### 3) Outputs

- Results are written in the **output directory** (the directory containing the input `.dat` file):
  - **`<basename>_results.txt`** — SAXS analysis results (Rg, I(0), Quality, Dmax, Porod volume, MW estimates).
  - **`<basename>.out`** — GNOM P(r) file.
- `<basename>` is the stem of the input `.dat` filename (e.g. `int_frame_001.dat` → `int_frame_001_results.txt` and `int_frame_001.out`).

## Verify the result

- Check that the **output directory** contains **`<basename>_results.txt`** and **`<basename>.out`** (non-empty), where `<basename>` corresponds to the input `.dat` filename stem.
- If the user reports failures, verify that the path to analysis is a valid, readable `.dat` file and that ATSAS is installed and on `PATH`.

## Common issues and solutions

- **autosaxs Python package is not installed**: Notify the user and recommend installing the package (e.g. from the project repository).
- **ATSAS not installed or not on PATH**: The script requires ATSAS. Recommend installing ATSAS and ensuring its binaries are on `PATH`.
- **Path to analysis missing or invalid**: The script will not run analysis without a valid `*.dat` path. Ensure the user provides a valid path and that it is passed correctly as a CLI argument.
- **Rg not determined**: The input `.dat` file may lack a valid Guinier region. Check the data quality or q-range.
