---
name: saxs-processing
description: Orchestrate SAXS data processing end-to-end — clarify goals, plan single- or multi-step workflows, and delegate execution to nested subskills. Use when the user works with small-angle X-ray scattering (2D frames, 1D curves, calibration, integrate, subtraction, analysis, plots, or fits).
---

# SAXS processing (orchestrator)

Use this skill as the **top-level SAXS orchestration layer** for the SAXS-specific skill bundle.

Subskills live as ordinary markdown under `saxs-processing/<name>/<name>.md` (not nested `SKILL.md` files). Only this orchestrator is a Cursor Agent Skill; open linked procedure docs when you need a step’s contract.

## When to use

- The user needs to **process, interpret, or fit SAXS data** (lab or synchrotron; 2D detector images or 1D reduced curves).
- The task may be a **single operation** or a **sequence** of steps (e.g. geometry → integration → subtraction → modelling).
- The user may **not** mention `autosaxs`; treat that name as the **default automation layer** for this bundle when you decide to run commands here.

## What you do

- Reframe the request in **scientific / data-flow terms** (what exists on disk, what is unknown, what quality checks matter).
- Produce a **concise plan**: ordered list of subskills when more than one step is useful; note dependencies and optional branches.
- **Open linked subskill `.md` files only when needed** for the step you are about to run or explain; refresh the plan as new artifacts appear.
- Tell the user your plan - what are you going to do, what will be the resulting artifacts. Present your plan in a user-friendly form, avoiding autosaxs-specific terms (fit-sizes, fit-distances) and instead using popular scientific terms ("fitting size distribution D(R)", "fitting pair distances distribution function P(r), characterizing the shape of particles"). Make your plan detailed - you are not going to just "calibrate" or "subtract", but you wanna use a specific image for calibration and specific curve as a buffer, and so on.
- Execute the plan upon agreement.
- You may **reorder, skip, or repeat** subskills when the user’s goal or intermediate results justify it (not only strict linear pipelines).


## What you should not do

- Replace the leaf skills’ own procedures, safety checks, or argument contracts when executing a step. Your processing should rely on the subskills, not procedures made-up from scratch.

## Subskill catalog

Each entry links to a procedure doc. Purpose and use-cases come from that file (and the embedded autosaxs docstring). Compact index (path → CLI hook → short teaser):

- [`calibrate/calibrate.md`](calibrate/calibrate.md) (`autosaxs calibrate`) — SAXS / small-angle x-ray scattering: calibrate detector geometry using calibrant image. This is a prerequisite for 'integrate' (azimuthal integration).
- [`integrate/integrate.md`](integrate/integrate.md) (`autosaxs integrate`) — SAXS / small-angle x-ray scattering: integrate 2D SAXS images to 1D curves (q, I, sigma) using a calibrated integrator produced by 'calibrate' (azimuthal integration; q-space).
- [`average/average.md`](average/average.md) (`autosaxs average`) — SAXS / small-angle x-ray scattering: radiation-damage-aware averaging of per-frame 1D SAXS curves.
- [`integrate-proxy/integrate-proxy.md`](integrate-proxy/integrate-proxy.md) (`autosaxs integrate-proxy`) — SAXS / small-angle x-ray scattering: integrate 2D TIFF image(s) to a 1D curve without detector calibration, using radial averaging in pixel-radius space (quick-look / debugging; not q-calibrated).
- [`subtract/subtract.md`](subtract/subtract.md) (`autosaxs subtract`) — SAXS / small-angle x-ray scattering: subtract a buffer curve from a sample 1D profile (background subtraction). Scaling uses either 'point_match' (default)
- [`plot/plot.md`](plot/plot.md) (`autosaxs plot`) — SAXS / small-angle x-ray scattering: generate standard diagnostic plots for a 1D curve (Guinier, Kratky, log-log):
- [`plot-2d/plot-2d.md`](plot-2d/plot-2d.md) (`autosaxs plot-2d`) — SAXS / small-angle x-ray scattering: render 2D SAXS TIFF image(s) to PNG using log-intensity scaling (2D detector view).
- [`fit-guinier/fit-guinier.md`](fit-guinier/fit-guinier.md) (`autosaxs fit-guinier`) — SAXS / small-angle x-ray scattering: Do Guinier analysis on a 1D profile (Rg, I(0), Rg span, Guinier interval, quality).
- [`analyze-kratky/analyze-kratky.md`](analyze-kratky/analyze-kratky.md) (`autosaxs analyze-kratky`) — SAXS / small-angle x-ray scattering: dimensionless Kratky conformation analysis on a 1D profile.
- [`fit-distances/fit-distances.md`](fit-distances/fit-distances.md) (`autosaxs fit-distances`) — SAXS / small-angle x-ray scattering: run ATSAS DATGNOM to obtain a pair distance distribution function \(p(r)\) for a monodisperse system from a 1D SAXS curve (real-space distance distribution).
- [`fit-sizes/fit-sizes.md`](fit-sizes/fit-sizes.md) (`autosaxs fit-sizes`) — SAXS / small-angle x-ray scattering: run ATSAS GNOM (system=1, spheres) to obtain a size distribution function \(D(R)\) for a polydisperse system from a 1D SAXS curve.
- [`model-dr-mc/model-dr-mc.md`](model-dr-mc/model-dr-mc.md) (`autosaxs model-dr-mc`) — SAXS / small-angle x-ray scattering: recover a form-free volume-weighted size distribution
- [`model-mixture/model-mixture.md`](model-mixture/model-mixture.md) (`autosaxs model-mixture`) — SAXS / small-angle x-ray scattering: run MIXTURE fits on a 1D subtracted curve, select the best model by BIC, and write a comparison plot, size distribution plot, and results CSV.
- [`model-bodies/model-bodies.md`](model-bodies/model-bodies.md) (`autosaxs model-bodies`) — SAXS / small-angle x-ray scattering: run ATSAS 'bodies' shape fitting for multiple candidate shapes on a 1D profile.
- [`model-dam/model-dam.md`](model-dam/model-dam.md) (`autosaxs model-dam`) — SAXS / small-angle x-ray scattering: ab initio bead-model shape reconstruction with ATSAS DAMMIF, optionally followed by DAMAVER ensemble averaging. When no GNOM '.out' is supplied, 'fit_distances' is run in-process to obtain one.
- [`model-density/model-density.md`](model-density/model-density.md) (`autosaxs model-density`) — SAXS / small-angle x-ray scattering: ab initio continuous electron-density reconstruction with DENSS (Grant protocol; density map / FSC resolution / voxel σ map).
- [`process-monodisperse/process-monodisperse.md`](process-monodisperse/process-monodisperse.md) (`autosaxs process-monodisperse`) — SAXS / small-angle x-ray scattering: run the monodisperse single-profile quality pipeline
- [`report-individual/report-individual.md`](report-individual/report-individual.md) (`autosaxs report-individual`) — SAXS / small-angle x-ray scattering: build a per-sample report from an existing pipeline directory.
- [`report-summary/report-summary.md`](report-summary/report-summary.md) (`autosaxs report-summary`) — SAXS / small-angle x-ray scattering: build a summary report for all samples in a pipeline directory.

## Sequencing

- Common patterns: calibration / geometry → azimuthal integration → buffer subtraction (if the notion of buffer is applicable)  → plots → analysis / fits.  
  - Typical monodisperse analysis: **fit-distances → model-bodies and model-dam or model-density** (bead models vs continuous density; prefer model-density for multi-contrast)  
  - Typical polydisperse analysis (assuming spherical shape of the particles): **fit-sizes → model-dr-mc → model-mixture**  
- State the sequence as explicit steps: **order → subskill path → rationale → what is still unknown or assumed**.  
- After a step completes, **revisit the plan** before pulling in additional leaf skills.  

## Reports (fragment contract)

- Processing skills write per-sample ``{basename}_report_individual.md`` and optional ``{basename}_report_summary.yaml`` next to their outputs (paths inside those files are relative to that directory).
- ``report-individual`` merges all ``*_report_individual.md`` under the pipeline root for a basename; ``report-summary`` merges all ``*_report_summary.yaml``.

## Inputs and outputs (orchestrator level)
- **Execution is strict only where the leaf says so:** when you actually run a subskill, follow its linked `.md` procedure for required arguments, configs, and environment rules.

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

- Generated by `autosaxs get-skills` (autosaxs 2.12.0).
