---
name: subtract
description: Runs buffer subtraction for 1D SAXS data to eliminate background scattering from solutions. The buffer curve is subtracted from the sample curve using a scaling factor derived from matching the high-q tail (configurable q-range). 
license: MIT
compatibility: opencode
metadata:
  category: processing
  tool: subtract.py
---

## What I do

- Run **SAXS buffer subtraction** by executing the **subtract.py** script in `skills/subtract/`.
- The script accepts **command-line arguments**: sample 1D path (`.dat`), buffer 1D path (`.dat`), and config file path (`.conf`, essentially YAML).
- **Working directory** is always derived from the sample path (the directory containing the sample file); the user does not provide it. All subtraction outputs are written there.
- Subtraction is always performed; existing result files are not skipped.
- Outputs written to the working directory: subtracted curve `sub_<basename>.dat`, difference plot `diff_<basename>.png`, and subtracted-curve plot `sub_<basename>.png`.

## When to use me

- You have **sample** and **buffer** 1D SAXS curves (`.dat`) and need to **subtract** the buffer from the sample with tail-matching scaling.
- You want to run buffer subtraction in a single step with explicit paths (sample, buffer, config) passed as CLI arguments.

## Command-line arguments (required inputs)

The **subtract.py** script accepts the following as command-line arguments:

1. **Sample path** — path to the sample 1D SAXS curve (e.g. `*.dat`).
2. **Buffer path** — path to the buffer 1D SAXS curve (e.g. `*.dat`).
3. **Config file path** — path to a YAML config file (e.g. `*.conf`). Must contain the key listed in "Config file (YAML) requirements" below.

## Requirements

To use this skill, your environment must have:
1. The `autosaxs` Python package installed (e.g. via pip from the project repository).

## What to clarify with the user (if not specified)

1. **Sample path** — full or relative path to the sample `.dat` file.
2. **Buffer path** — full or relative path to the buffer `.dat` file.
3. **Config file path** — full or relative path to the `.conf` (or other YAML) config file.

## Config file (YAML) requirements

The config file is loaded as YAML and must provide (at least) the following structure:

- **sub** — section for subtraction parameters:
  - **q_range_abs** — tuple `(q_start, q_stop)` (in 1/nm or same units as the data) defining the q-range used to scale the buffer to the sample (match high-q tail).

If the `sub` section or `q_range_abs` is missing, the script will fail.

## Step-by-step algorithm

### 1) Resolve paths and directory

- **Directory**: Always derived from the sample path (e.g. the parent directory of the sample file). The user does not provide it.
- **Sample path**: Must point to an existing `*.dat` file (1D SAXS sample curve).
- **Buffer path**: Must point to an existing `*.dat` file (1D SAXS buffer curve).
- **Config path**: Must point to an existing `*.conf` (or YAML) file.

### 2) Invoke the script

- Run **subtract.py** from `skills/subtract/` (or ensure it is on the invocation path), passing the required values as command-line arguments:
  - Sample path
  - Buffer path
  - Config file path
- Use the Python interpreter specified by the project (see workspace rules) when running the script.

Script usage example:
`python subtract.py SAMPLE.dat BUFFER.dat CONFIG.conf`

### 3) Outputs

- Subtraction results are written in the **working directory** (the directory containing the sample file):
  - `sub_<basename>.dat` — subtracted 1D curve (sample minus scaled buffer).
  - `diff_<basename>.png` — plot of sample vs scaled buffer.
  - `sub_<basename>.png` — plot of the subtracted curve.
- `<basename>` is derived from the sample filename (e.g. `foo.dat` → `foo`, then `sub_foo.dat`, `diff_foo.png`, `sub_foo.png`).

## Verify the result

- Check that the **working directory** contains:
  - `sub_<basename>.dat` (non-empty).
  - `diff_<basename>.png` and `sub_<basename>.png` (non-empty).
- If the user reports failures, verify that the config file contains the `sub` section with `q_range_abs` and that all paths (sample, buffer, config) are correct and readable.

## Common issues and solutions

- **autosaxs Python package is not installed**: Notify the user and recommend installing the package (e.g. from the project repository).
- **Config KeyError or missing keys**: The config YAML is missing the `sub` section or `q_range_abs`. Ask the user to supply a complete config or add the missing keys.
- **Sample or buffer path missing or invalid**: The script will not run subtraction without valid sample and buffer `.dat` paths. Ensure the user provides valid paths and that they are passed correctly as CLI arguments.
- **Sample and buffer q-grids differ**: The buffer may be interpolated onto the sample q-grid; if issues occur, suggest checking that both files are from compatible processing (e.g. same integration setup).
