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
- You may **reorder, skip, or repeat** subskills when the user’s goal or intermediate results justify it (not only strict linear pipelines).

## What you should not do

- Replace the leaf skills’ own procedures, safety checks, or argument contracts when executing a step. Your processing should rely on the subskills, not procedures made-up from scratch.

## Subskill catalog

The purpose and use-cases for each subskill can be derived from its **description** and full docstring inside its `SKILL.md`. The lines below are a compact index (path → CLI hook → short teaser):

- **`saxs-processing/calibrate`** (`autosaxs calibrate`) — SAXS / small-angle x-ray scattering: calibrate detector geometry using a calibration image and a config (ring-analysis + geometry refinement). This is a prerequisite for 'integrate' (azimuthal integration).
- **`saxs-processing/integrate`** (`autosaxs integrate`) — SAXS / small-angle x-ray scattering: integrate 2D SAXS images to 1D curves (q, I, sigma) using a calibrated integrator produced by 'calibrate' (azimuthal integration; q-space).
- **`saxs-processing/integrate-proxy`** (`autosaxs integrate-proxy`) — SAXS / small-angle x-ray scattering: integrate 2D TIFF image(s) to a 1D curve without detector calibration, using radial averaging in pixel-radius space (quick-look / debugging; not q-calibrated).
- **`saxs-processing/subtract`** (`autosaxs subtract`) — SAXS / small-angle x-ray scattering: subtract a buffer curve from a sample 1D profile (background subtraction). Scaling uses either 'point_match' (default)
- **`saxs-processing/plot`** (`autosaxs plot`) — SAXS / small-angle x-ray scattering: generate standard diagnostic plots for a 1D curve (Guinier, Kratky, log-log):
- **`saxs-processing/plot-2d`** (`autosaxs plot-2d`) — SAXS / small-angle x-ray scattering: render 2D SAXS TIFF image(s) to PNG using log-intensity scaling (2D detector view).
- **`saxs-processing/guinier-analysis`** (`autosaxs guinier-analysis`) — SAXS / small-angle x-ray scattering: run Guinier analysis on a 1D profile (Rg, I(0); multiple strategies such as first-interval fits and an adaptive choice). The skill writes:
- **`saxs-processing/fit-distances`** (`autosaxs fit-distances`) — SAXS / small-angle x-ray scattering: run ATSAS DATGNOM to obtain a pair distance distribution function \(p(r)\) for a monodisperse system from a 1D SAXS curve (real-space distance distribution).
- **`saxs-processing/fit-sizes`** (`autosaxs fit-sizes`) — SAXS / small-angle x-ray scattering: run ATSAS GNOM (system=1/5) to obtain a size distribution function \(D(R)\) for a polydisperse system from a 1D SAXS curve (polydispersity; spheres/rods).
- **`saxs-processing/fit-mixture`** (`autosaxs fit-mixture`) — SAXS / small-angle x-ray scattering: run MIXTURE fits on a 1D subtracted curve, select the best model by BIC, and write a comparison plot, size distribution plot, and results CSV (mixture / multi-population size distributions).
- **`saxs-processing/fit-bodies`** (`autosaxs fit-bodies`) — SAXS / small-angle x-ray scattering: run ATSAS 'bodies' shape fitting for multiple candidate shapes on a 1D profile, exporting fit files (FIR, PNG, YAML, CSV) and a comparison figure.
- **`saxs-processing/fit-dammif`** (`autosaxs fit-dammif`) — SAXS / small-angle x-ray scattering: run ATSAS 'dammif' (ab initio shape reconstruction) on a 1D profile (shape reconstruction / bead model). If a GNOM output file is available, you can provide it; otherwise the profile is used.
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
- ``report-individual`` merges all ``*_report_individual.md`` under the pipeline root for a basename; ``report-summary`` merges all ``*_report_summary.yaml``. PDFs are built with **ReportLab** from the merged Markdown.

## Inputs and outputs (orchestrator level)
- **Execution is strict only where the leaf says so:** when you actually run a subskill, follow its `SKILL.md` for required arguments, configs, and environment rules.

## Implementation note (`autosaxs`)

Nested folders under `saxs-processing/` correspond to the **autosaxs** Python package / CLI. When this bundle is the chosen implementation, prefer `<env>/bin/autosaxs <command>` (or the Python API noted in each leaf). Other SAXS-capable tools remain valid if the user or environment requires them — this bundle does not mandate autosaxs for high-level reasoning, only for invoking these leaves.

## Suggested orchestrator output

1. Restated goal in SAXS terms (short paragraph).
2. Planned **sequence** of subskill paths, or a single step if that suffices.
3. **Gaps and assumptions** (missing files, ambiguous sample/buffer pairing, unknown beamline metadata, …).
4. **Next action:** which leaf `SKILL.md` to read and what to run or ask for first.

## Provenance

- Generated by `autosaxs get-skills` (autosaxs 2.3.0).
