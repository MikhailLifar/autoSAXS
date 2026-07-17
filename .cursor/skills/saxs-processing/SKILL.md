---
name: saxs-processing
description: Orchestrate SAXS data processing end-to-end — clarify goals, plan single- or multi-step workflows, and delegate execution to nested subskills. Use when the user works with small-angle X-ray scattering (2D frames, 1D curves, calibration, integrate, subtraction, analysis, plots, or fits).
---

# SAXS processing (orchestrator)

Use this skill as the **top-level SAXS orchestration layer** for the SAXS-specific skill bundle.

## When to use

- The user needs to **process, interpret, or fit SAXS data** (lab or synchrotron; 2D detector images or 1D reduced curves).
- The task may be a **single operation** or a **sequence** of steps (e.g. geometry → integration → subtraction → modelling).
- The user may **not** mention `autosaxs`; treat that name as the **default automation layer** for this bundle when you decide to run commands here.

## What you do

- Reframe the request in **scientific / data-flow terms** (what exists on disk, what is unknown, what quality checks matter).
- Produce a **concise plan**: ordered list of subskills when more than one step is useful; note dependencies and optional branches.
- **Open leaf `SKILL.md` files only when needed** for the step you are about to run or explain; refresh the plan as new artifacts appear.
- Tell the user your plan - what are you going to do, what will be the resulting artifacts. Present your plan in a user-friendly form, avoiding autosaxs-specific terms (fit-sizes, fit-distances) and instead using popular scientific terms ("fitting size distribution D(R)", "fitting pair distances distribution function P(r), characterizing the shape of particles"). Make your plan detailed - you are not going to just "calibrate" or "subtract", but you wanna use a specific image for calibration and specific curve as a buffer, and so on.
- Execute the plan upon agreement.
- You may **reorder, skip, or repeat** subskills when the user’s goal or intermediate results justify it (not only strict linear pipelines).


## What you should not do

- Replace the leaf skills’ own procedures, safety checks, or argument contracts when executing a step. Your processing should rely on the subskills, not procedures made-up from scratch.

## Subskill catalog

The purpose and use-cases for each subskill can be derived from its **description** and full docstring inside its `SKILL.md`. The lines below are a compact index (path → CLI hook → short teaser):

- **`saxs-processing/calibrate`** (`autosaxs calibrate`) — SAXS / small-angle x-ray scattering: calibrate detector geometry using a calibration image and a config (ring-analysis + geometry refinement). This is a prerequisite for 'integrate' (azimuthal integration).
- **`saxs-processing/integrate`** (`autosaxs integrate`) — SAXS / small-angle x-ray scattering: integrate 2D SAXS images to 1D curves (q, I, sigma) using a calibrated integrator produced by 'calibrate' (azimuthal integration; q-space).
- **`saxs-processing/average`** (`autosaxs average`) — SAXS / small-angle x-ray scattering: radiation-damage-aware averaging of per-frame 1D curves.
- **`saxs-processing/integrate-proxy`** (`autosaxs integrate-proxy`) — SAXS / small-angle x-ray scattering: integrate 2D TIFF image(s) to a 1D curve without detector calibration, using radial averaging in pixel-radius space (quick-look / debugging; not q-calibrated).
- **`saxs-processing/subtract`** (`autosaxs subtract`) — SAXS / small-angle x-ray scattering: subtract a buffer curve from a sample 1D profile (background subtraction). Scaling uses either 'point_match' (default)
- **`saxs-processing/plot`** (`autosaxs plot`) — SAXS / small-angle x-ray scattering: generate standard diagnostic plots for a 1D curve (Guinier, Kratky, log-log):
- **`saxs-processing/plot-2d`** (`autosaxs plot-2d`) — SAXS / small-angle x-ray scattering: render 2D SAXS TIFF image(s) to PNG using log-intensity scaling (2D detector view).
- **`saxs-processing/fit-guinier`** (`autosaxs fit-guinier`) — SAXS / small-angle x-ray scattering: fit the Guinier region on a 1D profile (adaptive Rg, I(0), Rg span). Writes:
- **`saxs-processing/analyze-kratky`** (`autosaxs analyze-kratky`) — SAXS / small-angle x-ray scattering: dimensionless Kratky conformation analysis on a 1D profile.
- **`saxs-processing/fit-distances`** (`autosaxs fit-distances`) — SAXS / small-angle x-ray scattering: run ATSAS DATGNOM to obtain a pair distance distribution function \(p(r)\) for a monodisperse system from a 1D SAXS curve (real-space distance distribution).
- **`saxs-processing/fit-sizes`** (`autosaxs fit-sizes`) — SAXS / small-angle x-ray scattering: run ATSAS GNOM (system=1/5) to obtain a size distribution function \(D(R)\) for a polydisperse system from a 1D SAXS curve (polydispersity; spheres/rods).
- **`saxs-processing/fit-mixture`** (`autosaxs fit-mixture`) — SAXS / small-angle x-ray scattering: run MIXTURE fits on a 1D subtracted curve, select the best model by BIC, and write a comparison plot, size distribution plot, and results CSV (mixture / multi-population size distributions).
- **`saxs-processing/fit-bodies`** (`autosaxs fit-bodies`) — SAXS / small-angle x-ray scattering: run ATSAS 'bodies' shape fitting for multiple candidate shapes on a 1D profile, exporting fit files (FIR, PNG, YAML, CSV) and a comparison figure.
- **`saxs-processing/fit-dammif`** (`autosaxs fit-dammif`) — SAXS / small-angle x-ray scattering: run ATSAS 'dammif' (ab initio shape reconstruction) on a 1D profile (shape reconstruction / bead model). When no GNOM '.out' is supplied, 'fit_distances' is run in-process to obtain one.
- **`saxs-processing/report-individual`** (`autosaxs report-individual`) — SAXS / small-angle x-ray scattering: build a per-sample report from an existing pipeline directory.
- **`saxs-processing/report-summary`** (`autosaxs report-summary`) — SAXS / small-angle x-ray scattering: build a summary report for all samples in a pipeline directory.

## Sequencing

- Common patterns: calibration / geometry → azimuthal integration → buffer subtraction (if the notion of buffer is applicable)  → plots → analysis / fits.  
  - Typical monodisperse analysis: **fit-distances → fit-bodies and fit-dammif**  
  - Typical polydisperse analysis (assuming spherical shape of the particles): **fit-sizes → fit-mixture**  
- State the sequence as explicit steps: **order → subskill path → rationale → what is still unknown or assumed**.  
- After a step completes, **revisit the plan** before pulling in additional leaf skills.  

## Reports (fragment contract)

- Processing skills write per-sample ``{basename}_report_individual.md`` and optional ``{basename}_report_summary.yaml`` next to their outputs (paths inside those files are relative to that directory).
- ``report-individual`` merges all ``*_report_individual.md`` under the pipeline root for a basename; ``report-summary`` merges all ``*_report_summary.yaml``.

## Inputs and outputs (orchestrator level)
- **Execution is strict only where the leaf says so:** when you actually run a subskill, follow its `SKILL.md` for required arguments, configs, and environment rules.

## SAXS data I/O

When you need to inspect, transform, or create SAXS data files from Python, prefer the canonical helpers in `autosaxs.core.utils` instead of ad-hoc parsing or serialization. Import from the installed package, e.g. `from autosaxs.core.utils import read_saxs, parse_gnom_out`.

- `read_saxs(filename) -> (wavenumber, intensity, sigma, metadata)` — Read SAXS data and metadata from a file with YAML metadata and CSV data.
- `load_saxs_1d_any(filename: str) -> Tuple[np.ndarray, np.ndarray, Optional[np.ndarray]]` — Load q, I and optionally sigma from a SAXS 1D file.
- `write_saxs(filename, wavenumber, intensity, sigma, metadata)` — Write SAXS data and metadata to a file using YAML for metadata and CSV for data.
- `write_saxs_atsas_format(filename: str, q: np.ndarray, I: np.ndarray, sigma: Optional[np.ndarray] = None) -> None` — Write SAXS data in ATSAS .dat format: plain 3 columns (q, intensity, errors),
- `read_data(filename) -> (data, columns_as_arrays, metadata)` — Read generic tabular data and metadata from a file with YAML metadata and CSV data.
- `write_data(filename, data: pd.DataFrame, metadata)` — Write generic tabular data and metadata to a file using YAML for metadata and CSV for data.

- Preserve the pipeline unit convention from `autosaxs.core.utils`: `q` is in `nm^-1`, while `Rg`, `Dmax`, and other lengths are in `nm`.

## Implementation note (`autosaxs`)

Nested folders under `saxs-processing/` correspond to the **autosaxs** Python package / CLI. When this bundle is the chosen implementation, prefer `<env>/bin/autosaxs <command>` (or the Python API noted in each leaf). Other SAXS-capable tools remain valid if the user or environment requires them — this bundle does not mandate autosaxs for high-level reasoning, only for invoking these leaves.

## Provenance

- Generated by `autosaxs get-skills` (autosaxs 2.9.0).
